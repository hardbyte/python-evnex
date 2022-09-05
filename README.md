# Python EVNEX

Pull charging data from the rather sparsely documented EVNEX api.

## Installation

pip install git+https://github.com/hardbyte/python-evnex

## Examples

`python-evnex` is intended as a library, but a few example scripts are provided.

Providing authentication for the examples is via environment variables, e.g. on nix systems:

```
export EVNEX_CLIENT_USERNAME=you@example.com
export EVNEX_CLIENT_PASSWORD=<your password>

python -m examples.get_charge_point_detail
```
