# ASE2SPRKKR GUI

A PyQt6 desktop interface for creating and editing ASE structures, configuring
SPR-KKR calculations, and exploring calculation results through ASE2SPRKKR.

## Install

Install this beta alongside the matching released ASE2SPRKKR and KKRunner packages:

```bash
python -m pip install --pre ase2sprkkr-gui
```

## Run

```bash
ase2sprkkr-gui
```

The GUI requires the ASE2SPRKKR Python backend. Running actual SPR-KKR
calculations also requires a separately installed SPR-KKR executable.

## Development

Source: https://github.com/ase2sprkkr/ase2sprkkr-gui

## Optional scientific features

Matplotlib is installed with the GUI for structure visualization. Plotting
SPR-KKR result files may additionally need SciPy and scikit-image; ARPES NeXus
export needs HDF5/NeXus support; detailed element information uses Mendeleev.
They are not installed unless requested.

Open **Settings → Optional dependencies** to inspect available features and
install the extra packages with explicit confirmation. The GUI can also offer
installation when a plot or NeXus export action encounters a recognized missing
optional package.

Use `pip install 'ase2sprkkr-gui[plot]'`,
`pip install 'ase2sprkkr-gui[nexus]'`, or
`pip install 'ase2sprkkr-gui[mendeleev]'` for an upfront installation.
The core library remains the authority for optional scientific packages.

Installing into a running Python process is not always safe. If the GUI cannot
use a newly installed module, restart it. In externally managed Python
environments, install the GUI in a virtual environment instead.
