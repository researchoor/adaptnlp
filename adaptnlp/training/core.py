# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/12_training.core.ipynb (unless otherwise specified).

__all__ = ['Strategy', 'ParentLabeller', 'ColReader', 'Categorize', 'MultiCategorize', 'RandomSplitter', 'TaskDatasets',
           'AdaptiveDataLoaders', 'AdaptiveTuner']

# Cell
from fastcore.xtras import Path, range_of # pathlib `Path` with extra bits
from fastcore.foundation import mask2idxs, L
from fastcore.meta import delegates
from fastcore.basics import mk_class, listify

from fastai.torch_core import display_df, rank_distrib
from fastai.learner import Learner
from fastai.callback.core import CancelStepException
from fastai.callback.hook import Learner
from fastai.callback.progress import Learner
from fastai.callback.schedule import Learner

from functools import partial
# Patch'd Learner functionalities

from fastai.torch_core import display_df
from fastai.data.core import DataLoaders

from functools import partial

from torch.utils.data import DataLoader

from transformers import default_data_collator, AutoTokenizer, AutoModel
import torch

from typing import List, Union

import pandas as pd

# Cell
#nbdev_comment _all_ = ['Strategy']

# Cell
class ParentLabeller:
    """
    Extracts class based on filename's parent at `level`
    """
    def __init__(
        self,
        level=1 # The level up from `fname` to find the label
    ):
        self.level = level

    def __call__(self, o:Path): return self._do_level(o, self.level)

    def _do_level(self, o:Path, level:int):
        "Goes down one level on parent"
        def _inner(a): return a.parent
        if level == 1: return o.parent.name
        else: return self._do_level(_inner(o), level - 1)

# Cell
class ColReader:
    """
    Reads `cols` in `row` with potential `pref` and `suff`
    Based on the fastai class
    """
    def __init__(
        self,
        cols, # Some column names to use
        pref:str='', # A prefix
        suff:str='', # A suffix
        label_delim:str=None, # A label delimiter
    ):
        self.pref = str(pref) + os.path.sep if isinstance(pref, Path) else pref
        self.suff, self.label_delim = suff, label_delim
        self.cols = L(cols)

    def _do_one(self, r, c):
        o = r[c] if isinstance(c,int) else r[c] if c=='name' or c=='cat' else getattr(r,c)
        if len(self.pref)==0 and len(self.suff)==0 and self.label_delim is None: return o
        if self.label_delim is None: return f'{self.pref}{o}{self.suff}'
        else: return o.split(self.label_delim) if len(o)>0 else []

    def __call__(self, o):
        if len(self.cols) == 1: return self._do_one(o, self.cols[0])
        return L(self._do_one(o,c) for c in self.cols)

# Cell
class Categorize:
    """
    Collection of categories with reverse mapping in `o2i`
    Based on the fastai class
    """
    def __init__(
        self,
        names, # An interable collection of items to create a vocab from
        sort=True # Whether to make the items sorted
    ):
        names = L(names)
        self.classes = L(o for o in names.unique() if o == o)
        if sort: self.classes = self.classes.sorted()
        self.o2i = dict(self.classes.val2idx())

    def map_objs(
        self,
        objs # Some iterable collection
    ) -> L:
        "Map `objs` to IDs"
        return L(self.o2i[o] for o in objs)

    def map_ids(
        self,
        ids # Some ids correlating to `self.classes`
    ) -> L:
        "Map `ids` to objects in vocab"
        return L(self.classes[o] for o in ids)

    def __call__(self, o):
        "Label encode a single `o`"
        return int(self.o2i[o])

    def decode(
        self,
        o # A key in self.classes
    ):
        "Decodes `o` by looking in `self.classes`"
        return self.classes[o]

# Cell
class MultiCategorize(Categorize):
    """
    Collection of multi-categories with reverse mapping in `o2i`
    Based on the fastai class
    """
    def __init__(
        self,
        names, # An interable collection of items to create a vocab from
    ):
        super().__init__(names=names, sort=names==None)

    def __call__(self, o):
        "Label encode a single `o`"
        if not all(nm in self.o2i.keys() for nm in o):
            diff = [e for e in o if e not in self.o2i.keys()]
            diff_str = "', '".join(diff)
            raise KeyError(f"Labels '{diff_str}' were not included in the training dataset")

        return [int(self.o2i[o_]) for o_ in o]

    def decode(
        self,
        o # A list of keys in self.classes
    ) -> list:
        "Decodes `o` by looking in `self.classes`"
        return [self.classes[o_] for o_ in o]

# Cell
def RandomSplitter(valid_pct=0.2, seed=None):
    """
    Creates a function that splits some items between train and validation with `valid_pct` randomly
    Based on the fastai class
    """
    def _inner(o):
        if seed is not None: torch.manual_seed(seed)
        rand_idx = L(list(torch.randperm(len(o)).numpy()))
        cut = int(valid_pct * len(o))
        return rand_idx[cut:], rand_idx[:cut]
    return _inner

# Internal Cell
def _base_tok(item, tokenizer, tokenize_kwargs): return tokenizer(item, **tokenize_kwargs)

# Cell
class TaskDatasets:
    """
    A set of datasets for a particular task, with a simple API.

    Note: This is the base API, `items` should be a set of regular text and model-ready labels,
          including label or one-hot encoding being applied.
    """
    def __init__(
        self,
        train_dset, # A train `Dataset` object
        valid_dset, # A validation `Dataset` object
        tokenizer_name:str = None, # The string name of a `HuggingFace` tokenizer or model. If `None`, will not tokenize the dataset.
        tokenize:bool = True, # Whether to tokenize the dataset immediatly
        tokenize_func:callable = None, # A function to tokenize an item with
        tokenize_kwargs:dict = {}, # Some kwargs for when we call the tokenizer
        auto_kwargs:dict = {}, # Some kwargs when calling `AutoTokenizer.from_pretrained`
        remove_cols:Union[str,List[str]] = None, # What columns to remove
        label_keys:list=['labels'], # The keys in each item that relate to the label (such as `labels`)
    ):
        self.train = train_dset
        self.valid = valid_dset
        self.tokenizer = None
        self.label_keys = label_keys
        self.remove_cols = listify(remove_cols)
        if tokenizer_name is not None: self.set_tokenizer(tokenizer_name, **auto_kwargs)
        if tokenize_func is not None: self.tok_func = tokenize_func
        else: self.tok_func = _base_tok
        if self.tokenizer:
            if 'max_length' in tokenize_kwargs.keys() and self.tokenizer.model_max_length >= tokenize_kwargs['max_length']: pass
            elif 'max_length' in tokenize_kwargs.keys() and self.tokenizer.model_max_length < tokenize_kwargs['max_length']:
                print("Warning: `max_length` is larger than the pretrained model")
            elif 'max_length' not in tokenize_kwargs.keys():
                print("No value for `max_length` set, automatically adjusting to the size of the model and including truncation")
                tokenize_kwargs['max_length'] = self.tokenizer.model_max_length
                tokenize_kwargs['truncation'] = True
                print(f"Sequence length set to: {tokenize_kwargs['max_length']}")
        self.tokenize_kwargs = tokenize_kwargs
        if tokenize and self.tokenizer is not None: self._tokenize()
        elif tokenize and self.tokenizer is None:
            print("Tried to tokenize a dataset without a tokenizer. Please set a tokenizer with `set_tokenizer` and call `_tokenize()`")


    def __getitem__(self, idx): return self.train[idx]

    def _tokenize(self):
        "Tokenize dataset in `self.items` with `kwargs` for `tokenize()`"
        if not self.tokenizer: raise ValueError("Tried to tokenize a dataset without a tokenizer. Please add a tokenizer with `set_tokenizer(tokenizer_name` and try again")
        f = partial(self.tok_func, tokenizer=self.tokenizer, tokenize_kwargs=self.tokenize_kwargs)
        self.train = self.train.map(f,batched=True,remove_columns = self.remove_cols)
        self.valid = self.valid.map(f,batched=True,remove_columns = self.remove_cols)

    @delegates(AutoTokenizer.from_pretrained)
    def set_tokenizer(
        self,
        tokenizer_name:str, # A string name of a `HuggingFace` tokenizer or model
        override_existing:bool = False, # Whether to override an existing tokenizer
        **kwargs # kwargs to go to `AutoTokenizer.from_pretrained`
    ):
        "Sets a new `AutoTokenizer` to `self.tokenizer`"
        if self.tokenizer and not override_existing:
            print(f'Warning! You are trying to override an existing tokenizer: {self.tokenizer.name_or_path}. Pass `override_existing=True` to use a new tokenizer')
            return
        elif self.tokenizer and override_existing:
            print(f'Setting new tokenizer to {tokenizer_name}')
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_name, **kwargs)
        except:
            raise ValueError(f'{tokenizer_name} is not a valid pretrained model on the HuggingFace Hub or a local model')

    def set_classes(
        self,
        classes:list, # An ordered list of class names
    ):
        "Override the class labels in `self.categorize` it exists, otherwise make it"
        if hasasttr(self, 'categorize'):
            self.categorize.classes = L(classes).sorted()
            self.categorize.o2i = dict(self.categorize.val2idx())
        else:
            self.categorize = Categorize(classes)


    @delegates(DataLoaders)
    def dataloaders(
        self,
        batch_size:int=8, # A batch size
        shuffle_train:bool=True, # Whether to shuffle the training dataset
        collate_fn:callable = None, # A custom collation function
        **kwargs # Torch DataLoader kwargs
    ):
        "Creates `DataLoaders` from the dataset"
        if collate_fn is None: collate_fn = default_data_collator
        train_dl = DataLoader(self.train, shuffle=shuffle_train, collate_fn=collate_fn, batch_size=batch_size, **kwargs)
        valid_dl = DataLoader(self.valid, shuffle=False, collate_fn=collate_fn, batch_size=batch_size, **kwargs)
        return AdaptiveDataLoaders(train_dl, valid_dl, tokenizer=self.tokenizer, label_keys=self.label_keys)

# Cell
class AdaptiveDataLoaders(DataLoaders):
    "A set of `DataLoaders` that keeps track of a `tokenizer`"
    def __init__(
        self,
        *loaders, # A variable list of DataLoaders
        tokenizer=None, # A Transformers tokenizer object
        label_keys:list=['labels'], # A list of keys correlating to the labels in the batch
        path='.', # A path to be stored in `self.path`
        device=None # A device for the tensors, such as "cpu" or "cuda:0"
    ):
        self.tokenizer = tokenizer
        self.label_keys = label_keys
        super().__init__(*loaders, path=path, device=device)

    def show_batch(
        self,
        ds_idx:int=0, # Index of the DataLoader to show, 0 = training, 1 = validation
        n:int=5, # Number of examples to show
        raw:bool=False, # Prints the raw inputs and targets without decoding
    ):
        "Show a batch of data"
        dl = self[ds_idx]
        batch = next(iter(self[ds_idx]))

        if n > len(batch['input_ids']):
            print('`n` is larger than one batch, printing entire batch')
            n = len(batch['input_ids'])
        if n < 1:
            raise ValueError('Tried to show zero samples, please enter a value for `n` greater than 0')

        if not self.tokenizer and not raw:
            print("Cannot decode without a tokenizer, printing raw outputs..")

        if raw or not self.tokenizer:
            new_cols = []
            for nm in batch.keys():
                new_cols.append(nm)
                new_cols.append(f'{nm} Shape')
            df = pd.DataFrame(columns=new_cols)
            for i in range(n):
                new_row = []
                for key in batch.keys():
                    new_row += [batch[key][i].cpu().numpy()]
                    new_row += [batch[key][i].shape]
                df.loc[i] = new_row

        else:
            inputs = self.tokenizer.batch_decode(
                batch['input_ids'],
                skip_special_tokens=True
            )
            # Pair each label by item
            lbls = [[batch[l][i] for l in self.label_keys] for i in range(len(batch['input_ids']))]
            df = pd.DataFrame(columns=['Input'] + [l.title() for l in self.label_keys])
            for i in range(len(lbls)):
                decoded_lbl = []
                for j,key in enumerate(self.label_keys):
                    if key == 'labels' or key == 'label':
                        if hasattr(self, 'categorize'):
                            decoded_lbl.append(self.categorize.decode(lbls[i][j].cpu().numpy()))
#                             decoded_lbl.append([self.categorize.decode(o.cpu().numpy()) for o in lbls])
                        else:
                            # It's a language model
                            decoded_lbl.append(self.tokenizer.batch_decode(lbls[i][j].unsqueeze(0), skip_special_tokens=True)[0])
                    else:
                        decoded_lbl.append(lbls[i][j])
                lbls[i] = decoded_lbl
            for i in range(n):
                df.loc[i] = [inputs[i]] + lbls[i]
            display_df(df)

# Cell
class _AdaptiveLearner(Learner):
    """
    A base fastai `Learner` that overrides `_split` and `_do_one_batch` to
    have it work with HuggingFace datasets and models
    """
    def _split(self, b):
        "Assign `self.xb` to model input and labels"
        self.xb = b
        yb = []
        for label in self.label_keys:
            if label in b.keys():
                yb.append(b[label])
        self.yb = torch.stack(yb, dim=0)

    def _do_one_batch(self):
        "Move a batch of data to a device, get predictions, calculate the loss, and perform backward pass"
        self.xb = {k:v.to(self.device) for k,v in self.xb.items()} # See if `to_device` fixes this
        self.yb = self.yb.to(self.device)
        out = self.model(**self.xb)
        if 'loss' in out.keys():
            self.loss_grad = out['loss'].to(self.device)
        self.pred = out
        self('after_pred')
        self.loss = self.loss_grad.clone()
        self('after_loss')
        if not self.training or not len(self.yb): return
        self('before_backward')
        self.loss_grad.backward()
        self._with_events(self.opt.step, 'step', CancelStepException)
        self.opt.zero_grad()

# Cell
mk_class('Strategy', **{'OneCycle':'fit_one_cycle', 'CosineAnnealing':'fit_flat_cos', 'SGDR':'fit_sgdr'}, doc='Class for fitting strategies with typo-proofing')

# Cell
class AdaptiveTuner:
    "A base `Tuner` that interfaces with `AdaptiveLearner` with specific exposed functions"
    def __init__(
        self,
        expose_fastai:bool=False, # Whether to expose the entire API in `self`
        tokenizer = None, # A HuggingFace tokenizer
        label_keys:list=['labels'], # A list of keys correlating to the labels in the batch
        **kwargs # kwargs for `_AdaptiveLearner`
    ):
        self.tokenizer = tokenizer
        if label_keys is None:
            if getattr(kwargs['dls'], 'label_keys', None) is None:
                raise ValueError("Could not find keys for the labels. Please pass in a `label_keys` param")
            else:
                label_keys = kwargs['dls'].label_keys
        self._tuner = _AdaptiveLearner(**kwargs)
        self._tuner.label_keys = label_keys

        exposed_attrs = ['dls', 'model', 'loss_func', 'metrics']
        for attr in exposed_attrs:
            setattr(self, attr, getattr(self._tuner, attr))
        if expose_fastai:
            cls = self.__class__
            self.__class__ = cls.__class__("AdaptiveTuner", (cls, _AdaptiveLearner), kwargs)

    def tune(
        self,
        epochs:int, # Number of iterations to train for
        lr:float = None, # If None, finds a new learning rate and uses suggestion_method
        strategy:Strategy = Strategy.OneCycle, # A fitting method
        callbacks:list = [], # Extra fastai Callbacks
        **kwargs ## kwargs for the fit function
    ):
        "Fine tune `self.model` for `epochs` with an `lr` and `strategy`"
        func = getattr(self, strategy, getattr(self._tuner, strategy, None))
        for attr in 'epochs,lr,cbs'.split():
            if attr in kwargs.keys(): kwargs.pop(attr)
        func(epochs, lr, cbs=callbacks, **kwargs)

    @delegates(Learner.lr_find)
    def lr_find(
        self,
        **kwargs # Learner.lr_find kwargs
    ):
        "Runs fastai's `LR Finder`"
        return self._tuner.lr_find(**kwargs)

    def save(
        self,
        save_directory # A folder to save our model to
    ):
        "Save a pretrained model to a `save_directory`"
        if rank_distrib(): return # Don't save if child proc
        self.model.save_pretrained(save_directory)
        self.tokenizer.save_pretrained(save_directory)
        return save_directory

    def load(
        self,
        path:Union[Path,str], # A location to load a tokenizer and weights from
        device = None # A valid device such as `cpu` or `cuda:0`
    ):
        "Loads a pretrained model with `AutoModel.from_pretrained` from `path` and loads it to device"
        if device is None and hasattr(self.dls, 'device'): device = self.dls.device
        self.model = AutoModel.from_pretrained(path)
        self.model = self.model.to(device)
        self.tokenizer = AutoTokenizer.from_pretrained(path)
        return self

    def predict(
        self,
        text:Union[List[str], str] # Some text or list of texts to inference with
    ):
        "Predict some `text` with the current model. Needs to be implemented for each task separately"
        raise NotImplementedError()

    def export(
        self,
        save_directory:Union[Path,str] # A folder to export our model to
    ):
        "Exports the current model and tokenizer information to `save_directory`"
        raise NotImplementedError()