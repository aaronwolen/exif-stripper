"""Test the CLI."""

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from exif_stripper import cli

RUNNING_ON_WINDOWS = platform.system() == 'Windows'

if not RUNNING_ON_WINDOWS:
    from xattr import setxattr, xattr


def copy_with_extended_attributes(src, dst, extra_attributes=None):
    """
    Copy file with extended attributes

    Copies extended attributes with xattr because shutil.copystat() fails to
    preserve the 'com.apple.macl' attribute.

    Parameters
    ----------
    src : str
        Source file path.
    dst : str
        Destination file path.
    extra_attributes : dict, optional
        Extra attributes to add to the destination file, by default {}.
    """
    shutil.copy(src, dst)
    attributes = xattr(src)
    for attribute in attributes:
        setxattr(dst, attribute, attributes[attribute])
    if extra_attributes:
        xattr(dst).update(extra_attributes or {})
    return dst


def supports_xattr():
    """Check if the filesystem supports xattr."""
    try:
        with tempfile.NamedTemporaryFile(delete=True) as tmp:
            xattr(tmp.name).set('the.limit.does.not.exist', b'\x00')
            return True
    except (IOError, OSError):
        return False


XATTR_SUPPORTED = supports_xattr()


@pytest.fixture
def test_dir():
    """
    Fixture for a temp directory within the tests folder.

    Required to reproduce the inability to remove the com.apple.macl attribute,
    which behaves differently when the file is in a temp directory.
    """
    test_dir = Path(os.path.dirname(os.path.abspath(__file__))) / 'temp'
    test_dir.mkdir(exist_ok=True)
    yield test_dir
    shutil.rmtree(test_dir)


@pytest.fixture
def image_with_exif_data(tmp_path):
    """Fixture for an image with EXIF data."""
    image_file = tmp_path / 'test.png'
    with Image.new(mode='1', size=(2, 2)) as im:
        exif = im.getexif()
        exif[274] = 2
        im.save(image_file, exif=exif)

    return image_file


@pytest.fixture
def image_with_metadata(image_with_exif_data):
    """Fixture for an image with metadata."""
    if XATTR_SUPPORTED:
        xattr(image_with_exif_data).set('the.limit.does.not.exist', b'\x00')
    return image_with_exif_data


@pytest.fixture
def real_image_with_metadata(test_dir):
    test_image_path = Path('tests/data/python-logo-master-v3-TM.png')

    temp_image_path = test_dir / test_image_path.name
    if temp_image_path.exists():
        temp_image_path.unlink()

    return copy_with_extended_attributes(
        test_image_path,
        temp_image_path,
        extra_attributes={'the.limit.does.not.exist': b'\x00'},
    )


def has_metadata(filepath, on_windows) -> bool:
    """Utility to check if a file has metadata."""
    with Image.open(filepath) as im:
        has_exif = dict(im.getexif()) != {}
        if on_windows or not XATTR_SUPPORTED:
            return has_exif
        return has_exif or bool(xattr(filepath).list())


def assert_metadata_stripped(filepath, on_windows=RUNNING_ON_WINDOWS):
    """Checks that a file that had metadata before no longer does."""
    assert has_metadata(filepath, on_windows)

    has_changed = cli.process_image(filepath)

    assert not has_metadata(filepath, on_windows)
    assert has_changed

    has_changed = cli.process_image(filepath)
    assert not has_changed


@pytest.mark.skipif(
    RUNNING_ON_WINDOWS or not XATTR_SUPPORTED,
    reason='xattr does not work on Windows or is not supported',
)
def test_process_image_full(image_with_metadata, monkeypatch):
    """Test that cli.process_image() removes EXIF and extended attributes."""
    assert_metadata_stripped(image_with_metadata)


def test_process_image_exif_only(image_with_exif_data, monkeypatch):
    """Test that cli.process_image() removes EXIF only (Windows version)."""
    if not RUNNING_ON_WINDOWS:
        monkeypatch.setattr(platform, 'system', lambda: 'Windows')
    assert_metadata_stripped(image_with_exif_data, on_windows=True)


@pytest.mark.parametrize('exists', [True, False])
def test_process_image_file_issues(tmp_path, exists):
    """Test that cli.process_image() continues if files don't exist or aren't images."""
    file = tmp_path / 'test.txt'
    if exists:
        file.touch()

    has_changed = cli.process_image(file)
    assert not has_changed


def test_real_image_with_metadata(real_image_with_metadata):
    """Test that a real image with metadata is processed correctly."""
    assert_metadata_stripped(real_image_with_metadata)


@pytest.mark.skipif(not XATTR_SUPPORTED, reason='Filesystem does not support xattr')
def test_main(tmp_path, image_with_metadata):
    """Test that cli.main() returns the number of files altered."""
    file_without_metadata = tmp_path / 'clean.png'
    file_without_metadata.touch()

    files_changed = cli.main([str(file_without_metadata), str(image_with_metadata)])

    assert files_changed == 1


def test_cli_version(capsys):
    """Confirm that --version works."""
    with pytest.raises(SystemExit):
        cli.main(['--version'])
    assert f'{cli.PROG} {cli.__version__}' == capsys.readouterr().out.strip()


@pytest.mark.parametrize(['flag', 'return_code'], [['--version', 0], ['', 1]])
@pytest.mark.skipif(not XATTR_SUPPORTED, reason='Filesystem does not support xattr')
def test_main_access_cli(flag, return_code, image_with_metadata):
    """Confirm that CLI can be accessed via python -m."""
    result = subprocess.run(
        [sys.executable, '-m', 'exif_stripper.cli', flag or str(image_with_metadata)]
    )
    assert result.returncode == return_code
