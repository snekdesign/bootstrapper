from collections.abc import Mapping
import concurrent.futures
import contextlib
import ctypes
import functools
import logging
import os
import subprocess
import sys
import threading
from typing import Any
from typing import BinaryIO
from typing import Literal
from typing import override
from typing import Required
from typing import TypedDict
import urllib.parse
import warnings

import pooch.typing  # pyright: ignore[reportMissingTypeStubs]
import pydantic_settings
import pylnk3  # pyright: ignore[reportMissingTypeStubs]
import rich.logging
import tqdm.rich

_BIN = os.path.abspath(os.path.join(__file__, '../bin'))


def main():
    _setup_logging()
    os.makedirs(_BIN, exist_ok=True)
    pydantic_settings.CliApp.run(_Main)


class _Downloader(pooch.typing.Downloader):
    def __init__(self, headers: Mapping[str, str]):
        self._impl = functools.partial(
            pooch.HTTPDownloader,
            headers=dict(headers),
        )

    @override
    def __call__(
        self,
        url: str,
        output_file: Any,
        pooch: pooch.Pooch | None,
        *,
        check_only: bool | None = None,
    ):
        desc = os.path.basename(urllib.parse.urlparse(url).path)
        with contextlib.ExitStack() as stack:
            if isinstance(output_file, str):
                output_file = stack.enter_context(open(output_file, 'w+b'))
            progressbar = _ProgressBar(output_file, desc)
            stack.callback(progressbar.close)
            downloader = self._impl(progressbar)
            return downloader(url, output_file, pooch)


class _File(TypedDict, total=False):
    url: Required[str]
    known_hash: str
    headers: Mapping[str, str]
    exposed: Mapping[str, str]
    lnk: bool


class _Main(pydantic_settings.BaseSettings):
    platform: pydantic_settings.CliPositionalArg[
        Literal['linux-64', 'osx-64', 'osx-arm64', 'win-64']
    ]

    def cli_cmd(self):
        _main(**getattr(_Settings(), self.platform.replace('-', '_')))


def _main(files: list[_File] = []):
    with concurrent.futures.ThreadPoolExecutor() as pool:
        with _lock, warnings.catch_warnings(
            action='ignore',
            category=tqdm.TqdmExperimentalWarning,
        ):
            prog = tqdm.rich.tqdm(
                concurrent.futures.as_completed([
                    pool.submit(_retrieve, **kw) for kw in files
                ]),
                desc='bootstrapping',
                total=len(files),
                unit='file',
            )
        with prog:
            for fut in prog:
                if e := fut.exception():
                    _logger.error('', exc_info=e)


class _PlatformSettings(TypedDict, total=False):
    files: list[_File]


class _ProgressBar(Any):
    def __init__(self, output_file: BinaryIO, desc: str):
        self._desc = desc
        self._output_file = output_file

    def close(self):
        if hasattr(self, '_impl'):
            self._impl.close()

    def reset(self):
        self._impl.n = 0

    def update(self, n: int, /):
        self._impl.update(n)

    @property
    def total(self):
        return self._impl.total

    @total.setter
    def total(self, value: int):
        if self._output_file.seekable():
            self._output_file.truncate(self._output_file.tell() + value)
        with _lock, warnings.catch_warnings(
            action='ignore',
            category=tqdm.TqdmExperimentalWarning,
        ):
            self._impl = tqdm.rich.tqdm(
                desc=self._desc,
                total=value,
                unit='B',
                unit_scale=True,
            )


def _retrieve(
    url: str,
    known_hash: str | None = None,
    headers: Mapping[str, str] = {},
    exposed: Mapping[str, str] = {},
    lnk: bool = False,
):
    downloader = _Downloader(headers)
    if url.endswith('.zip'):
        files = list(
            pooch.retrieve(  # pyright: ignore[reportUnknownMemberType]
                url,
                known_hash,
                processor=pooch.Unzip(),
                downloader=downloader,
            ),
        )
    else:
        files = [pooch.retrieve(url, known_hash, downloader=downloader)]  # pyright: ignore[reportUnknownMemberType]
    for k, v in exposed.items():
        src = min([fname for fname in files if fname.endswith(v)], key=len)
        if lnk and sys.platform == 'win32':
            if not os.path.splitext(k)[1]:
                k += '.lnk'
            dst = os.path.join(_BIN, k)
            if not (
                os.path.lexists(dst)
                and os.path.samefile(src, pylnk3.Lnk(dst).path)  # pyright: ignore[reportArgumentType, reportUnknownMemberType]
            ):
                try:
                    os.unlink(dst)
                except FileNotFoundError:
                    pass
                pylnk3.for_file(src, dst, window_mode=pylnk3.WINDOW_MAXIMIZED)  # pyright: ignore[reportUnknownMemberType]
        else:
            if sys.platform == 'win32' and not os.path.splitext(k)[1]:
                k += os.path.splitext(src)[1]
            dst = os.path.join(_BIN, k)
            if not (os.path.lexists(dst) and os.path.samefile(src, dst)):
                try:
                    os.unlink(dst)
                except FileNotFoundError:
                    pass
                try:
                    os.link(src, dst)
                except OSError:
                    os.symlink(src, dst)


class _Settings(pydantic_settings.BaseSettings):
    linux_64: _PlatformSettings = {}
    osx_64: _PlatformSettings = {}
    osx_arm64: _PlatformSettings = {}
    win_64: _PlatformSettings = {}

    model_config = pydantic_settings.SettingsConfigDict(
        alias_generator=lambda x: x.replace('_', '-'),
        pyproject_toml_table_header=('tool', 'bootstrapper'),
    )

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[pydantic_settings.BaseSettings],
        init_settings: pydantic_settings.PydanticBaseSettingsSource,
        env_settings: pydantic_settings.PydanticBaseSettingsSource,
        dotenv_settings: pydantic_settings.PydanticBaseSettingsSource,
        file_secret_settings: pydantic_settings.PydanticBaseSettingsSource,
    ):
        return (
            pydantic_settings.PyprojectTomlConfigSettingsSource(settings_cls),
        )


def _setup_logging():
    handler = rich.logging.RichHandler()
    logging.basicConfig(handlers=[handler])
    handler.setFormatter(None)
    pooch.utils.LOGGER = logger = logging.getLogger('pooch')
    logger.setLevel(logging.INFO)
    _logger.setLevel(logging.INFO)
    logging.captureWarnings(True)


_lock = threading.Lock()
_logger = logging.getLogger(__name__)

if __name__ == '__main__':
    if sys.platform == 'win32' and not ctypes.windll.shell32.IsUserAnAdmin():
        subprocess.run(['sudo', '-E', sys.executable, __file__], check=True)
    else:
        main()
