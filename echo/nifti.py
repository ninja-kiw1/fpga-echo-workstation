"""Minimal NIfTI-1 reader (.nii / .nii.gz) -- enough for CAMUS, no extra packages.

read_nifti(path) -> (array, spacing)
  array:   numpy array indexed [z, y, x] (2D files give [y, x]), i.e. the
           file's x-fastest voxel order reshaped C-style;
  spacing: pixdim[1..ndim] in file order (x, y, z), usually millimetres.
Scaling (scl_slope / scl_inter) is applied only when the slope is non-zero
and not 1, and then the result is float64.
"""
import gzip
import struct

import numpy as np

DTYPES = {2: np.uint8, 4: np.int16, 8: np.int32, 16: np.float32, 64: np.float64,
          256: np.int8, 512: np.uint16, 768: np.uint32}


def read_nifti(path):
    raw = open(path, 'rb').read()
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    for endian in '<>':
        if struct.unpack(endian + 'i', raw[:4])[0] == 348:
            break
    else:
        raise ValueError(f'{path}: not a NIfTI-1 header')
    if raw[344:347] not in (b'n+1', b'ni1'):
        raise ValueError(f'{path}: bad magic {raw[344:348]!r}')
    dim = struct.unpack(endian + '8h', raw[40:56])
    datatype = struct.unpack(endian + 'h', raw[70:72])[0]
    pixdim = struct.unpack(endian + '8f', raw[76:108])
    vox_offset = int(struct.unpack(endian + 'f', raw[108:112])[0])
    slope, inter = struct.unpack(endian + '2f', raw[112:120])
    ndim = dim[0]
    shape = dim[1:1 + ndim]
    dtype = np.dtype(DTYPES[datatype]).newbyteorder(endian)
    count = int(np.prod(shape))
    data = np.frombuffer(raw, dtype=dtype, count=count, offset=vox_offset)
    arr = data.reshape(shape[::-1]).astype(dtype.newbyteorder('='))
    if slope not in (0.0, 1.0) or (slope != 0.0 and inter != 0.0):
        arr = arr * float(slope) + float(inter)
    return arr, tuple(float(p) for p in pixdim[1:1 + ndim])
