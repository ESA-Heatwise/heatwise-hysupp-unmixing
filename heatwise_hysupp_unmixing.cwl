cwlVersion: v1.2
$namespaces:
  s: https://schema.org/
s:softwareVersion: 1.0.0
s:version: 1.0.0
schemas:
  - http://schema.org/version/9.0/schemaorg-current-http.rdf
$graph:
  - class: Workflow
    id: main
    label: HEATWISE HySUPP Unmixing Workflow
    doc: |
      EOAP-oriented supervised FCLS unmixing processor for CHIME-mimicked
      hyperspectral products.
    requirements: []
    inputs:
      - id: input_catalog
        type: Directory
        label: input catalog
        doc: Directory with stac catalog referencing input CHIME-mimicked hyperspectral GeoTIFF.
      - id: endmembers
        type: File
        label: endmembers
        doc: Endmember spectral library in CSV or XLSX format.
      - id: wavelengths_txt
        type: File?
        label: wavenlenghts_txt
        doc: Optional wavelength text file for the input image.
      - id: output_dir
        type: string
        label: output_dir
        default: "."
        doc: Output directory inside the CWL working directory.
      - id: output_prefix
        type: string
        label: output_prefix
        default: "unmixing"
        doc: Prefix used for output filenames.
      - id: input_nodata
        type: float?
        label: input_nodata
        doc: Optional input nodata override.
      - id: scale_factor
        type: float
        label: scale_factor
        default: 1.0
        doc: Multiplicative scale factor applied to the input image.
      - id: min_abundance
        type: float
        label: min_abundance
        default: 0.0
        doc: Minimum abundance required for top-1 class assignment.
      - id: maxiter
        type: int
        label: maxiter
        default: 200
        doc: Maximum number of FCLS iterations per pixel.
      - id: ftol
        type: double
        label: ftol
        default: 1.0e-8
        doc: FCLS numerical tolerance.
      - id: log_every
        type: int
        label: log_every
        default: 10000
        doc: Print FCLS progress every N pixels. Use 0 to disable.
    steps: 
      extract_input_image:
        run: '#extract_stac_catalog'
        in: 
          input_catalog: input_catalog
        out:
          - image_path 
      processor:
        run: '#hysupp_unmixing_processor'
        in:
          input_image: extract_input_image/image_path
          endmembers: endmembers
          wavelengths_txt: wavelengths_txt
          output_dir: output_dir
          output_prefix: output_prefix
          input_nodata: input_nodata
          scale_factor: scale_factor
          min_abundance: min_abundance
          maxiter: maxiter
          ftol: ftol
          log_every: log_every
        out:
          - output
    outputs:
      output:
        type: Directory
        outputSource: processor/output
  - class: CommandLineTool
    id: hysupp_unmixing_processor
    label: HEATWISE HySUPP Unmixing Processor
    baseCommand: ["/app/processor.py"]
    doc: |
      EOAP-oriented supervised FCLS unmixing processor for CHIME-mimicked
      hyperspectral products.

    requirements:
      DockerRequirement:
        dockerPull: ghcr.io/esa-heatwise/heatwise-hysupp-unmixing:v1.0.0
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
      output:
        type: Directory
        outputBinding:
          glob: "."
  - class: CommandLineTool
    id: extract_stac_catalog
    label: Transform stac catalog input to file input
    baseCommand: ["/app/extract_path_from_stac.py"]
    doc: |
        Processor that takes a Directory + STAC catalog input and transforms it to a file input
    requirements:
      DockerRequirement:
        dockerPull: ghcr.io/leonelgl/heatwise-hysupp-unmixing:v1.0.0
    inputs:
      input_catalog:
        type: Directory
        label: input catalog
        doc: Directory with stac catalog referencing input CHIME-mimicked hyperspectral GeoTIFF.
        inputBinding:
          prefix: --input-catalog
    outputs:
      image_path:
        type: File
        doc: Path to the image referenced by the input STAC catalog
        outputBinding:
            glob: input_file.tif
