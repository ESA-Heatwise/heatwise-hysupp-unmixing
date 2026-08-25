from pathlib import Path
import argparse
import shutil

import pystac

def paths_from_stac_catalog(catalog: pystac.Catalog) -> list[Path]:
    """
    Extracts asset paths from STAC catalog
    """
    assets = [Path(_cog_asset_from_item(item).href) for item in catalog.get_items()]
    return assets

def _cog_asset_from_item(item: pystac.Item) -> pystac.Asset:
    """
    Extract single asset with COG media type from pystac Item.
    Raises ValueError, if more than one such item is found
    """
    cog_assets = item.get_assets(media_type=pystac.MediaType.COG)
    if not len(cog_assets) == 1:
        raise ValueError(f"item '{item.id}' contains more than one COG asset.")
    v = next(iter(cog_assets.values()))

    return v

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-catalog")
    args = parser.parse_args()

    input_dir_path = Path(args.input_catalog) 
    catalog_path =  input_dir_path / "catalog.json"

    catalog = pystac.Catalog.from_file(catalog_path)
    catalog.set_self_href(catalog_path)
    paths = paths_from_stac_catalog(catalog)

    if len(paths) != 1:
        raise RuntimeError(f"The input catalog may only reference a single COG asset. Found '{len(paths)}'")

    path = input_dir_path / next(iter(paths))

    shutil.copy(path, "input_file.tif")


if __name__ == '__main__':
    main()
