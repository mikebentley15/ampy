import io
import shutil
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable, Optional

from pkg_resources import EntryPoint

from ampy.core.mpy_boards import MpyBoard
from ampy.core.settings import TMP_DIR
from ampy.core.util import clean_copy


def main(board: MpyBoard, entrypoint: Optional[str], modules: Iterable[Path]) -> Path:
    main_py = generate_main_py(entrypoint, modules)
    print("-" * 10 + " main.py " + "-" * 10, main_py, "-" * 29, sep="\n")

    with TemporaryDirectory() as dir:
        tmp_main_py = Path(dir) / "main.py"
        tmp_main_py.write_text(main_py)

        with clean_copy(
            # include generated main.py file in build
            (tmp_main_py, board.modules_dir / "main.py"),
            # include specified modules in build
            *((module, board.modules_dir / module.name) for module in modules),
        ):
            board.build()
            return shutil.copy(
                board.firmware_path,
                TMP_DIR
                / f"{board.chip}-firmware@{datetime.now().strftime('%d-%m-%Y_%I-%M-%S_%p')}.bin",
            )


AUTOGENERATED_WARNING = """\
# Generated by ampy!
# You probably don't need to modify this file. 
# It shall be automatically overwritten without warning.
"""


def generate_main_py(entrypoint: Optional[str], modules: Iterable[Path]) -> str:
    with io.StringIO() as f:
        f.write(AUTOGENERATED_WARNING)
        if entrypoint is not None:
            # parse entrypoint string of the format "<module>:<attrs>"
            entrypoint_obj = EntryPoint.parse(f"name={entrypoint} [extras]")

            # write import statement
            f.write(f"import {entrypoint_obj.module_name}\n")

            # write function call if provided
            if entrypoint_obj.attrs:
                f.write(f"{entrypoint_obj.module_name}.{entrypoint_obj.attrs[0]}()\n")
        else:
            # if no explicit entrypoint was provided, use the modules
            for module in modules:
                # if it's a package, import the __main__.py file
                # else, just import the module.
                if module.is_dir():
                    if not (module / "__main__.py").exists():
                        continue
                    f.write(f"import {module.name}.__main__\n")
                    break
                else:
                    f.write(f"import {module.name}\n")
                    break

        return f.getvalue()