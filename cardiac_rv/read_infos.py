"""Read CardiacUDA official infos.npy without executing arbitrary pickle code (only numpy array reconstruction allowed)."""
import io
import pickle
import sys
import zipfile  # noqa: F401

import numpy as np

ALLOWED = {('numpy.core.multiarray', '_reconstruct'), ('numpy._core.multiarray', '_reconstruct'),
           ('numpy', 'ndarray'), ('numpy', 'dtype'), ('numpy.core.multiarray', 'scalar'),
           ('numpy._core.multiarray', 'scalar'), ('collections', 'OrderedDict'), ('collections', 'defaultdict'),
           ('builtins', 'dict'), ('builtins', 'list')}


class Safe(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) in ALLOWED:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f'blocked {module}.{name}')


def load(path):
    raw = open(path, 'rb').read()
    f = io.BytesIO(raw)
    version = np.lib.format.read_magic(f)
    hdr = np.lib.format._read_array_header(f, version)
    assert hdr[2] == np.dtype('O'), hdr
    obj = Safe(f).load()
    return obj


if __name__ == '__main__':
    o = load(sys.argv[1])
    print(type(o), getattr(o, 'shape', None), getattr(o, 'dtype', None))
    d = o.item() if isinstance(o, np.ndarray) and o.shape == () else o
    print(type(d), len(d))
    items = list(d.items())
    for k, v in items[:3]:
        print(repr(k), v)
    import collections
    print(collections.Counter(v.get('dataset_name') for v in d.values()))
    print(collections.Counter(tuple(sorted(v.keys())) for v in d.values()))
