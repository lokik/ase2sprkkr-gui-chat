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
ase2sprkkr-gui Fe.cif             # open a structure or potential file
ase2sprkkr-gui calculation.out    # open a SPR-KKR calculation result
ase2sprkkr-gui results/           # import output files from a directory
ase2sprkkr-gui --open-last        # open the latest existing recent structure
ase2sprkkr-gui --job RUN_ID       # follow an active persistent job or open its result
ase2sprkkr-gui --output data.txt  # disambiguate a nonstandard file extension
```

The same options work with `ase2sprkkr gui`. A positional filename is
interpreted as an output for `.out`/`.log`, input parameters for
`.inp`/`.in`, and otherwise as a structure. Use `--structure FILE`,
`--input FILE`, or `--output PATH` to choose explicitly. `--open-last`
uses the recent **structure** history (skipping files that no longer exist),
even when a calculation output was opened more recently. Use `--job ID`
to resume monitoring an unfinished persistent calculation or open the
saved result of a finished one. The monitor displays the job ID when it
is available.

The GUI requires the ASE2SPRKKR Python backend. Running actual SPR-KKR
calculations also requires a separately installed SPR-KKR executable.

## Development

Source: https://github.com/ase2sprkkr/ase2sprkkr-gui

## Embedded cluster editor (development)

The GUI `cluster` branch requires the matching ASE2SPRKKR backend `cluster`
branch, which provides `HostSite`, `EmbeddingSymmetry` and
`SPRKKRAtoms.from_embedding`.

From a loaded 3D host or a supported 2D host, choose
**Structure → Build Embedded Cluster** (or the Guided workflow action). The
dialog edits explicit host-site occupations and displacements, supports
centre-preserving symmetry, and adds surrounding host sites by distance or
coordination shell. Cancel does not change the current structure.

Plain ASE host structures are sufficient to design a cluster, but native
**Save Structure** exports a native CLUPOT only when the cluster references
a host SPR-KKR Potential. Complete radial data are retained where supported;
partially available radial data cause a validation error rather than a lossy export. Generic ASE exports are disabled for embedded clusters
because they would lose their host-site mapping. The native **Load from File** action recognizes CLUPOT and reopens it with
an existing host Potential or asks for the host POTFIL if none is active.
Submitting a native embedding calculation is not yet implemented.
