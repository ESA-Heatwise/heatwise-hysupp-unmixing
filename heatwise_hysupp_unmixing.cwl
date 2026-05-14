cwlVersion: v1.2
class: CommandLineTool

label: HEATWISE HySUPP Unmixing Processor
doc: |
  EOAP-oriented supervised FCLS unmixing processor for CHIME-mimicked
  hyperspectral products.

requirements:
  DockerRequirement:
    dockerPull: ghcr.io/leonelgl/heatwise-hysupp-unmixing:v1.0.0
  InlineJavascriptRequirement: {}

inputs:
  input_image:
    type: File
    doc: Input CHIME-mimicked hyperspectral GeoTIFF.
    inputBinding:
      prefix: --input-image
      position: 1

  endmembers:
    type: File
    doc: Endmember spectral library in CSV or XLSX format.
    inputBinding:
      prefix: --endmembers
      position: 2

  wavelengths_txt:
    type: File?
    doc: Optional wavelength text file for the input image.
    inputBinding:
      prefix: --wavelengths-txt
      position: 3

  output_dir:
    type: string
    default: "."
    doc: Output directory inside the CWL working directory.
    inputBinding:
      prefix: --output-dir
      position: 4

  output_prefix:
    type: string
    default: "unmixing"
    doc: Prefix used for output filenames.
    inputBinding:
      prefix: --output-prefix
      position: 5

  input_nodata:
    type: float?
    doc: Optional input nodata override.
    inputBinding:
      prefix: --input-nodata
      position: 6

  scale_factor:
    type: float
    default: 1.0
    doc: Multiplicative scale factor applied to the input image.
    inputBinding:
      prefix: --scale-factor
      position: 7

  min_abundance:
    type: float
    default: 0.0
    doc: Minimum abundance required for top-1 class assignment.
    inputBinding:
      prefix: --min-abundance
      position: 8

  maxiter:
    type: int
    default: 200
    doc: Maximum number of FCLS iterations per pixel.
    inputBinding:
      prefix: --maxiter
      position: 9

  ftol:
    type: double
    default: 1.0e-8
    doc: FCLS numerical tolerance.
    inputBinding:
      prefix: --ftol
      position: 10

  log_every:
    type: int
    default: 10000
    doc: Print FCLS progress every N pixels. Use 0 to disable.
    inputBinding:
      prefix: --log-every
      position: 11

outputs:
  abundance_stack:
    type: File
    doc: FCLS abundance stack as Cloud Optimized GeoTIFF.
    outputBinding:
      glob: $(inputs.output_dir + "/" + inputs.output_prefix + "_abundance_stack_COG.tif")

  classification_map:
    type: File
    doc: Top-1 classification map as Cloud Optimized GeoTIFF.
    outputBinding:
      glob: $(inputs.output_dir + "/" + inputs.output_prefix + "_classification_map_COG.tif")

  class_names:
    type: File
    doc: JSON file containing the class label mapping.
    outputBinding:
      glob: $(inputs.output_dir + "/" + inputs.output_prefix + "_class_names.json")

  wavelengths_used:
    type: File
    doc: Wavelengths used by the FCLS processor after spectral matching.
    outputBinding:
      glob: $(inputs.output_dir + "/" + inputs.output_prefix + "_wavelengths_used_um.txt")

  run_metadata:
    type: File
    doc: JSON metadata describing the processing run.
    outputBinding:
      glob: $(inputs.output_dir + "/" + inputs.output_prefix + "_run_metadata.json")
