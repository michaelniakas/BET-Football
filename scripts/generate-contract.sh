#!/usr/bin/env sh
# Regenerates api/contract.py from openapi.yaml. CI runs this and fails if the result differs
# from the committed file, so run it after every contract change.
set -eu
cd "$(dirname "$0")/.."
datamodel-codegen \
  --input openapi.yaml --input-file-type openapi --output api/contract.py \
  --output-model-type pydantic_v2.BaseModel --target-python-version 3.11 \
  --enum-field-as-literal all --use-type-alias --use-annotated --field-constraints \
  --use-standard-collections --use-union-operator --strict-nullable --strict-types str int float bool \
  --disable-timestamp --formatters builtin
