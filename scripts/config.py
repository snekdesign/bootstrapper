import sys
from typing import override
from typing import Required
from typing import TypedDict

import pydantic_settings
import rattler


class File(TypedDict, total=False):
    url: Required[str]
    known_hash: str
    headers: dict[str, str]
    exposed: dict[str, str]
    lnk: bool


class PlatformSettings(TypedDict, total=False):
    files: list[File]


class Settings(pydantic_settings.BaseSettings):
    linux_64: PlatformSettings = PlatformSettings()
    osx_64: PlatformSettings = PlatformSettings()
    osx_arm64: PlatformSettings = PlatformSettings()
    win_64: PlatformSettings = PlatformSettings()

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


def main():
    platform = str(rattler.Platform.current()).replace('-', '_')
    sys.stdout.write(repr(getattr(Settings(), platform)))


if __name__ == '__main__':
    main()
