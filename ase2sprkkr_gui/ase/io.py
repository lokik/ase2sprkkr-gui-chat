"""Defer ASE IO and its optional import chain until a structure is used."""


def read(*args, **kwargs):
    from ase.io import read as ase_read

    return ase_read(*args, **kwargs)


def write(*args, **kwargs):
    from ase.io import write as ase_write

    return ase_write(*args, **kwargs)
