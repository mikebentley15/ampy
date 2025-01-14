# Adafruit MicroPython Tool - Command Line Interface
# Author: Tony DiCola
# Copyright (c) 2016 Adafruit Industries
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from __future__ import print_function
import hashlib
import os
import pathlib
import platform
import posixpath
import re
import serial.serialutil
import sys

import click
import dotenv

# Load AMPY_PORT et al from .ampy file
# Performed here because we need to beat click's decorators.
config = dotenv.find_dotenv(filename=".ampy", usecwd=True)
if config:
    dotenv.load_dotenv(dotenv_path=config)

import ampy.files as files
import ampy.pyboard as pyboard


_board = None


def windows_full_port_name(portname):
    # Helper function to generate proper Windows COM port paths.  Apparently
    # Windows requires COM ports above 9 to have a special path, where ports below
    # 9 are just referred to by COM1, COM2, etc. (wacky!)  See this post for
    # more info and where this code came from:
    # http://eli.thegreenplace.net/2009/07/31/listing-all-serial-ports-on-windows-with-python/
    m = re.match("^COM(\d+)$", portname)
    if m and int(m.group(1)) < 10:
        return portname
    else:
        return "\\\\.\\{0}".format(portname)


@click.group(
    context_settings={
        'auto_envvar_prefix': 'AMPY',
        'help_option_names': ['-h', '--help'],
    },
)
@click.option(
    "--port",
    "-p",
    required=True,
    type=click.STRING,
    help="Name of serial port for connected board.  Can optionally specify with AMPY_PORT environment variable.",
    metavar="PORT",
)
@click.option(
    "--baud",
    "-b",
    default=115200,
    type=click.INT,
    help="Baud rate for the serial connection (default 115200).  Can optionally specify with AMPY_BAUD environment variable.",
    metavar="BAUD",
)
#@click.option(
#    "--delay",
#    "-d",
#    default=0,
#    type=click.FLOAT,
#    help="Delay in seconds before entering RAW MODE (default 0). Can optionally specify with AMPY_DELAY environment variable.",
#    metavar="DELAY",
#)
@click.version_option()
def cli(port, baud): #, delay):
    """ampy - Adafruit MicroPython Tool

    Ampy is a tool to control MicroPython boards over a serial connection.  Using
    ampy you can manipulate files on the board's internal filesystem and even run
    scripts.
    """
    global _board
    # On Windows fix the COM port path name for ports above 9 (see comment in
    # windows_full_port_name function).
    if platform.system() == "Windows":
        port = windows_full_port_name(port)
    _board = pyboard.Pyboard(port, baudrate=baud) #, rawdelay=delay)


@cli.command()
@click.argument("remote_files", metavar="remote_file", nargs=-1)
@click.argument(
    "local_path",
    type=click.Path(allow_dash=True, path_type=pathlib.Path),
    required=False
)
@click.option("--verbose", "-v", is_flag=True, help="Print verbose updates")
def get(remote_files, local_path, verbose):
    """
    Retrieve a file from the board.

    Get will download one or more files from the board and print its contents or save it
    locally.  You must pass at least one argument which is the path to a file
    to download from the board.  If you specify more than one argument, the
    last one is the destination.  If you specify exactly one remote file and
    one local path, then the local path can either be a currently existing
    directory or a destination file path.  If you specify more than one remote
    file (i.e., three or more arguments), then the last argument must be a
    local directory that exists.

    If only one argument is specified, or if you pass in "-" as the second
    argument, then the contents of the specified remote file will be printed to
    standard output.  This is only available for one remote file.

    Note: If the destination file exists, it will be overwritten!

    For example to retrieve the boot.py and print it out run:

      ampy --port /board/serial/port get boot.py

    You can also specify "-" as the destination to explicitly specify to output
    to standard output.

    Or to get main.py and helper.py and save them in the current directory
    locally, run:

      ampy --port /board/serial/port get main.py helper.py ./
    """
    # if one argument was specified, interpret it as a remote file
    if not remote_files and local_path:
        remote_files = (str(local_path),)
        local_path = None

    # checks
    if len(remote_files) == 0:
        raise click.UsageError("Must specify at least one remote file")
    if len(remote_files) > 1 and not local_path.is_dir():
        raise click.UsageError(
                f"Invalid value for '[LOCAL_PATH]': Directory '{local_path}' does not exist.")

    board_files = files.Files(_board)

    def get_file(remote_file, destination):
        "Only called on files and not directories"
        # Get the file contents.
        contents = board_files.get(remote_file)

        # Print the file out if no local file was provided, otherwise save it.
        if destination is None or str(destination) == "-":
            print(contents.decode("utf-8"))
        else:
            remote_path = pathlib.Path(remote_file)
            if destination.is_dir():
                destination /= remote_path.name
            if verbose:
                print(remote_file, "->", str(destination))
            with destination.open(mode='wb') as local_file:
                local_file.write(contents)

    for remote_file in remote_files:
        remote_file = board_files.canonicalize_remote_path(remote_file)
        if board_files.isdir(remote_file):
            remote_path = pathlib.Path(remote_file)

            if not local_path or local_path.is_file():
                raise click.UsageError(
                    f"Remote directory needs a local directory destination")

            # If local path is an existing directory and remote is not root,
            # then we will create a local directory with the same name as the
            # remote
            current_local_dir = local_path
            if current_local_dir.is_dir() and not remote_file == "/":
                current_local_dir /= remote_path.name

            # Use our recursive ls() function to list all files and empty
            # directories to get
            tree = board_files.ls(remote_file, long_format=False, recursive=True)

            # Get each one, maintaining the relative remote directory structure
            for subpath in (pathlib.Path(x) for x in tree):
                relpath = subpath.relative_to(remote_path)
                current_local_file = current_local_dir.joinpath(relpath)
                # make sure the directory exists locally
                current_local_file.parent.mkdir(parents=True, exist_ok=True)
                if board_files.isdir(str(subpath)):
                    current_local_file.mkdir(exist_ok=True)
                else:
                    # copy the file contents over
                    get_file(str(subpath), current_local_file)
        else:
            # It's just a normal file, so just get it
            get_file(remote_file, local_path)



@cli.command()
@click.option(
    "--exists-okay", is_flag=True, help="Ignore if the directory already exists."
)
@click.option(
    "--make-parents", is_flag=True, help="Create any missing parents."
)
@click.argument("directory")
def mkdir(directory, exists_okay, make_parents):
    """
    Create a directory on the board.

    Mkdir will create the specified directory on the board.  One argument is
    required, the full path of the directory to create.

    By default you cannot recursively create a hierarchy of directories with one
    mkdir command. You may create each parent directory with separate
    mkdir command calls, or use the --make-parents option.
    
    For example to make a directory under the root called 'code':

      ampy --port /board/serial/port mkdir /code
      
    To make a directory under the root called 'code/for/ampy', along with all
    missing parents:

      ampy --port /board/serial/port mkdir --make-parents /code/for/ampy
    """
    # Run the mkdir command.
    board_files = files.Files(_board)
    if make_parents:
        if directory[0] != '/':
            directory = "/" + directory
        dirpath = ""
        for dir in directory.split("/")[1:-1]:
            dirpath += "/" + dir
            board_files.mkdir(dirpath, exists_okay=True)
    board_files.mkdir(directory, exists_okay=exists_okay)


@cli.command()
@click.argument("directory", default="/")
@click.option(
    "--long_format",
    "-l",
    is_flag=True,
    help="Print long format info including size of files.  Note the size of directories is not supported and will show 0 values.",
)
@click.option(
    "--recursive",
    "-r",
    is_flag=True,
    help="recursively list all files and (empty) directories.",
)
def ls(directory, long_format, recursive):
    """List contents of a directory on the board.

    Can pass an optional argument which is the path to the directory.  The
    default is to list the contents of the root, /, path.

    For example to list the contents of the root run:

      ampy --port /board/serial/port ls

    Or to list the contents of the /foo/bar directory on the board run:

      ampy --port /board/serial/port ls /foo/bar

    Add the -l or --long_format flag to print the size of files (however note
    MicroPython does not calculate the size of folders and will show 0 bytes):

      ampy --port /board/serial/port ls -l /foo/bar
    """
    # List each file/directory on a separate line.
    board_files = files.Files(_board)
    for f in board_files.ls(directory, long_format=long_format, recursive=recursive):
        print(f)


@cli.command()
@click.argument("local", type=click.Path(exists=True))
@click.argument("remote", required=False)
@click.option("--verbose", "-v", is_flag=True, help="Print verbose updates")
@click.option("--strip", "-s", is_flag=True, help="Strip docstrings and comments")
@click.option("--checksum", "-c", is_flag=True, help="Skip files with equal checksum")
def put(local, remote, verbose, strip, checksum):
    """Put a file or folder and its contents on the board.

    Put will upload a local file or folder  to the board.  If the file already
    exists on the board it will be overwritten with no warning!  You must pass
    at least one argument which is the path to the local file/folder to
    upload.  If the item to upload is a folder then it will be copied to the
    board recursively with its entire child structure.  You can pass a second
    optional argument which is the path and name of the file/folder to put to
    on the connected board.

    For example to upload a main.py from the current directory to the board's
    root run:

      ampy --port /board/serial/port put main.py

    Or to upload a board_boot.py from a ./foo subdirectory and save it as boot.py
    in the board's root run:

      ampy --port /board/serial/port put ./foo/board_boot.py boot.py

    To upload a local folder adafruit_library and all of its child files/folders
    as an item under the board's root run:

      ampy --port /board/serial/port put adafruit_library

    Or to put a local folder adafruit_library on the board under the path
    /lib/adafruit_library on the board run:

      ampy --port /board/serial/port put adafruit_library /lib/adafruit_library
    """
    # Use the local filename if no remote filename is provided.
    if remote is None:
        remote = os.path.basename(os.path.abspath(local))
    board_files = files.Files(_board)

    def copy_file(local_filepath, remote_filepath):
        with open(local_filepath, "rb") as infile:
            contents = infile.read()

            # try to strip the python file if requested
            stripped = False
            if strip and local_filepath.endswith(".py"):
                try:
                    old_contents = contents
                    contents = files.strip_docstrings_and_comments(contents)
                except:
                    # not a hard error, just push the old contents
                    print("Warning: could not strip", local_filepath)
                else:
                    stripped = True
                    contents = contents.encode("utf-8")

            checksum_matches = False
            if checksum:
                try:
                    remotehash = board_files.checksum(remote_filepath)
                except:
                    pass
                else:
                    localhash = hashlib.sha256(contents).hexdigest()
                    checksum_matches = (remotehash == localhash.encode("utf-8"))

            # print information about the copy and stripping process
            if verbose:
                msg = "{} {} -> {}".format(
                        "skip" if checksum_matches else "copy",
                        local_filepath, remote_filepath)
                if stripped:
                    msg += "  ({} bytes -> {} bytes)".format(
                            len(old_contents), len(contents))
                print(msg)

            # copy the (potentially stripped) contents to the board
            if not checksum_matches:
                board_files.put(remote_filepath, contents)
                return len(contents)
            return 0

    bytes_written = 0

    # Check if path is a folder and do recursive copy of everything inside it.
    # Otherwise it's a file and should simply be copied over.
    if os.path.isdir(local):
        # Directory copy, create the directory and walk all children to copy
        # over the files.
        for parent, child_dirs, child_files in os.walk(local, followlinks=True):
            # Create board filesystem absolute path to parent directory.
            remote_parent = posixpath.normpath(
                posixpath.join(remote, os.path.relpath(parent, local))
            )
            try:
                if verbose: print("mkdir", remote_parent)
                # Create remote parent directory.
                board_files.mkdir(remote_parent)
            except files.DirectoryExistsError:
                # Ignore errors for directories that already exist.
                pass
            # Loop through all the files and put them on the board too.
            for filename in child_files:
                filepath = os.path.join(parent, filename)
                remote_filepath = posixpath.join(remote_parent, filename)
                bytes_written += copy_file(filepath, remote_filepath)
    else:
        bytes_written += copy_file(local, remote)

    if verbose:
        print()
        print("Wrote {} bytes to the micropython board".format(bytes_written))


@cli.command()
@click.argument("remote_files", metavar="remote_file", nargs=-1)
@click.option("--verbose", "-v", is_flag=True, help="Print verbose updates")
@click.option("--force", "-f", is_flag=True, help="ignore nonexistent files")
def rm(remote_files, verbose, force):
    """Remove one or more files from the board.

    Remove the specified file(s) from the board's filesystem.  Note that this
    can't delete directories which have files inside them, but can delete empty
    directories.

    For example to delete main.py and data.txt from the root of a board run:

      ampy --port /board/serial/port rm main.py data.txt
    """
    board_files = files.Files(_board)
    for filepath in remote_files:
        if verbose: print("rm -f" if force else "rm", filepath)
        try:
            board_files.rm(filepath)
        except RuntimeError:
            if not force:
                raise
            elif verbose:
                print("Warning:", filepath, "does not exist", file=sys.stderr)


@cli.command()
@click.option(
    "--missing-okay", is_flag=True, help="Ignore if the directory does not exist."
)
@click.argument("remote_folder")
def rmdir(remote_folder, missing_okay):
    """Forcefully remove a folder and all its children from the board.

    Remove the specified folder from the board's filesystem.  Must specify one
    argument which is the path to the folder to delete.  This will delete the
    directory and ALL of its children recursively, use with caution!

    For example to delete everything under /adafruit_library from the root of a
    board run:

      ampy --port /board/serial/port rmdir adafruit_library
    """
    # Delete the provided file/directory on the board.
    board_files = files.Files(_board)
    board_files.rmdir(remote_folder, missing_okay=missing_okay)


@cli.command()
@click.argument(
    "local_file",
    type=click.Path(readable=True, allow_dash=True)
)
@click.option(
    "--no-output",
    "-n",
    is_flag=True,
    help="Run the code without waiting for it to finish and print output.  Use this when running code with main loops that never return.",
)
def run(local_file, no_output):
    """Run a script and print its output.

    Run will send the specified file to the board and execute it immediately.
    Any output from the board will be printed to the console (note that this is
    not a 'shell' and you can't send input to the program).

    Note that if your code has a main or infinite loop you should add the --no-output
    option.  This will run the script and immediately exit without waiting for
    the script to finish and print output.

    For example to run a test.py script and print any output until it finishes:

      ampy --port /board/serial/port run test.py

    Or to run test.py and not wait for it to finish:

      ampy --port /board/serial/port run --no-output test.py
    """
    # Run the provided file and print its output.
    board_files = files.Files(_board)
    try:
        output = board_files.run(local_file, not no_output, not no_output)
        if output is not None:
            print(output.decode("utf-8"), end="")
    except IOError:
        click.echo(
            "Failed to find or read input file: {0}".format(local_file), err=True
        )

@cli.command("exec")
@click.argument("command")
@click.option(
    "--no-output",
    "-n",
    is_flag=True,
    help="Run the command without waiting for it to finish and print output.  Use this when running code with main loops that never return.",
)
def exec_(command, no_output):
    """Run a command and print its output.

    Run will send the specified command to the board and execute it immediately.
    Any output from the board will be printed to the console (note that this is
    not a 'shell' and you can't send input to the program).

    Note that if your code has a main or infinite loop you should add the --no-output
    option.  This will run the command and immediately exit without waiting for
    the script to finish and print output.

    For example to run a command to print the system information:

      ampy --port /board/serial/port exec "import os; print(os.uname())"

    Or to run test.py and not wait for it to finish:

      $ ampy exec --no-output "
      > import time, machine
      > while True:
      >   time.sleep(0.8)
      >   machine.Pin(25, machine.Pin.OUT).toggle()
      > "
    """
    board_files = files.Files(_board)
    output = board_files.exec_(command, not no_output, not no_output)
    if output is not None:
        print(output.decode("utf-8"), end="")

@cli.command()
@click.option(
    "--bootloader", "mode", flag_value="BOOTLOADER", help="Reboot into the bootloader"
)
@click.option(
    "--hard",
    "mode",
    flag_value="NORMAL",
    help="Perform a hard reboot, including running init.py",
)
@click.option(
    "--repl",
    "mode",
    flag_value="SOFT",
    default=True,
    help="Perform a soft reboot, entering the REPL  [default]",
)
@click.option(
    "--safe",
    "mode",
    flag_value="SAFE_MODE",
    help="Perform a safe-mode reboot.  User code will not be run and the filesystem will be writeable over USB",
)
def reset(mode):
    """Perform soft reset/reboot of the board.

    Will connect to the board and perform a reset.  Depending on the board
    and firmware, several different types of reset may be supported.

      ampy --port /board/serial/port reset
    """
    _board.enter_raw_repl()
    if mode == "SOFT":
        _board.exit_raw_repl()
        return

    _board.exec_(
        """if 1:
        def on_next_reset(x):
            try:
                import microcontroller
            except:
                if x == 'NORMAL': return ''
                return 'Reset mode only supported on CircuitPython'
            try:
                microcontroller.on_next_reset(getattr(microcontroller.RunMode, x))
            except ValueError as e:
                return str(e)
            return ''
        def reset():
            try:
                import microcontroller
            except:
                import machine as microcontroller
            microcontroller.reset()
    """
    )
    r = _board.eval("on_next_reset({})".format(repr(mode)))
    print("here we are", repr(r))
    if r:
        click.echo(r, err=True)
        return

    try:
        _board.exec_raw_no_follow("reset()")
    except serial.serialutil.SerialException as e:
        # An error is expected to occur, as the board should disconnect from
        # serial when restarted via microcontroller.reset()
        pass


if __name__ == "__main__":
    try:
        cli()
    finally:
        # Try to ensure the board serial connection is always gracefully closed.
        if _board is not None:
            try:
                _board.close()
            except:
                # Swallow errors when attempting to close as it's just a best effort
                # and shouldn't cause a new error or problem if the connection can't
                # be closed.
                pass
