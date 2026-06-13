#!/usr/bin/env bash
set -euo pipefail

# Optional Docker OCR runner for machines without a healthy host Tesseract.
# Host Tesseract is preferred when available; this wrapper preserves a fallback.

if [[ $# -lt 2 ]]; then
  echo "usage: scripts/run_ocr_container.sh INPUT_IMAGE OUTPUT_BASE [LANG=ben] [PSM=6]" >&2
  exit 2
fi

input_image=$1
output_base=$2
lang=${3:-ben}
psm=${4:-6}

input_dir=$(cd "$(dirname "$input_image")" && pwd)
input_name=$(basename "$input_image")
output_dir=$(mkdir -p "$(dirname "$output_base")" && cd "$(dirname "$output_base")" && pwd)
output_name=$(basename "$output_base")

docker run --rm \
  -v "$input_dir:/input:ro" \
  -v "$output_dir:/output" \
  jitesoft/tesseract-ocr \
  tesseract "/input/$input_name" "/output/$output_name" -l "$lang" --psm "$psm"
