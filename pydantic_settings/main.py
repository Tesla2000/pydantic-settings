from __future__ import annotations as _annotations

import asyncio
import inspect
import threading
from argparse import Namespace
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, ClassVar, TypeVar

from pydantic import ConfigDict
from pydantic._internal._config import config_keys
from pydantic._internal._signature import _field_name_for_signature
from pydantic._internal._utils import deep_update, is_model_class
from pydantic.dataclasses import is_pydantic_dataclass
from pydantic.main import BaseModel

from .exceptions import SettingsError
from .sources import (
    ENV_FILE_SENTINEL,
    CliSettingsSource,
    DefaultSettingsSource,
    DotEnvSettingsSource,
    DotenvType,
    EnvSettingsSource,
    InitSettingsSource,
    PathType,
    PydanticBaseSettingsSource,
    PydanticModel,
    SecretsSettingsSource,
    get_subcommand,
)

T = TypeVar('T')


class SettingsConfigDict(ConfigDict, total=False):
    case_sensitive: bool
    nested_model_default_partial_update: bool | None
    env_prefix: str
    env_file: DotenvType | None
    env_file_encoding: str | None
    env_ignore_empty: bool
    env_nested_delimiter: str | None
    env_nested_max_split: int | None
    env_parse_none_str: str | None
    env_parse_enums: bool | None
    cli_prog_name: str | None
    cli_parse_args: bool | list[str] | tuple[str, ...] | None
    cli_parse_none_str: str | None
    cli_hide_none_type: bool
    cli_avoid_json: bool
    cli_enforce_required: bool
    cli_use_class_docs_for_groups: bool
    cli_exit_on_error: bool
    cli_prefix: str
    cli_flag_prefix_char: str
    cli_implicit_flags: bool | None
    cli_ignore_unknown_args: bool | None
    cli_kebab_case: bool | None
    cli_shortcuts: Mapping[str, str | list[str]] | None
    secrets_dir: PathType | None
    json_file: PathType | None
    json_file_encoding: str | None
    yaml_file: PathType | None
    yaml_file_encoding: str | None
    yaml_config_section: str | None
    """
    Specifies the top-level key in a YAML file from which to load the settings.
    If provided, the settings will be loaded from the nested section under this key.
    This is useful when the YAML file contains multiple configuration sections
    and you only want to load a specific subset into your settings model.
    """

    pyproject_toml_depth: int
    """
    Number of levels **up** from the current working directory to attempt to find a pyproject.toml
    file.

    This is only used when a pyproject.toml file is not found in the current working directory.
    """

    pyproject_toml_table_header: tuple[str, ...]
    """
    Header of the TOML table within a pyproject.toml file to use when filling variables.
    This is supplied as a `tuple[str, ...]` instead of a `str` to accommodate for headers
    containing a `.`.

    For example, `toml_table_header = ("tool", "my.tool", "foo")` can be used to fill variable
    values from a table with header `[tool."my.tool".foo]`.

    To use the root table, exclude this config setting or provide an empty tuple.
    """

    toml_file: PathType | None
    enable_decoding: bool


# Extend `config_keys` by pydantic settings config keys to
# support setting config through class kwargs.
# Pydantic uses `config_keys` in `pydantic._internal._config.ConfigWrapper.for_model`
# to extract config keys from model kwargs, So, by adding pydantic settings keys to
# `config_keys`, they will be considered as valid config keys and will be collected
# by Pydantic.
config_keys |= set(SettingsConfigDict.__annotations__.keys())


class BaseSettings(BaseModel):
    """
    Base class for settings, allowing values to be overridden by environment variables.

    This is useful in production for secrets you do not wish to save in code, it plays nicely with docker(-compose),
    Heroku and any 12 factor app design.

    All the below attributes can be set via `model_config`.

    Args:
        _case_sensitive: Whether environment and CLI variable names should be read with case-sensitivity.
            Defaults to `None`.
        _nested_model_default_partial_update: Whether to allow partial updates on nested model default object fields.
            Defaults to `False`.
        _env_prefix: Prefix for all environment variables. Defaults to `None`.
        _env_file: The env file(s) to load settings values from. Defaults to `Path('')`, which
            means that the value from `model_config['env_file']` should be used. You can also pass
            `None` to indicate that environment variables should not be loaded from an env file.
        _env_file_encoding: The env file encoding, e.g. `'latin-1'`. Defaults to `None`.
        _env_ignore_empty: Ignore environment variables where the value is an empty string. Default to `False`.
        _env_nested_delimiter: The nested env values delimiter. Defaults to `None`.
        _env_nested_max_split: The nested env values maximum nesting. Defaults to `None`, which means no limit.
        _env_parse_none_str: The env string value that should be parsed (e.g. "null", "void", "None", etc.)
            into `None` type(None). Defaults to `None` type(None), which means no parsing should occur.
        _env_parse_enums: Parse enum field names to values. Defaults to `None.`, which means no parsing should occur.
        _cli_prog_name: The CLI program name to display in help text. Defaults to `None` if _cli_parse_args is `None`.
            Otherwise, defaults to sys.argv[0].
        _cli_parse_args: The list of CLI arguments to parse. Defaults to None.
            If set to `True`, defaults to sys.argv[1:].
        _cli_settings_source: Override the default CLI settings source with a user defined instance. Defaults to None.
        _cli_parse_none_str: The CLI string value that should be parsed (e.g. "null", "void", "None", etc.) into
            `None` type(None). Defaults to _env_parse_none_str value if set. Otherwise, defaults to "null" if
            _cli_avoid_json is `False`, and "None" if _cli_avoid_json is `True`.
        _cli_hide_none_type: Hide `None` values in CLI help text. Defaults to `False`.
        _cli_avoid_json: Avoid complex JSON objects in CLI help text. Defaults to `False`.
        _cli_enforce_required: Enforce required fields at the CLI. Defaults to `False`.
        _cli_use_class_docs_for_groups: Use class docstrings in CLI group help text instead of field descriptions.
            Defaults to `False`.
        _cli_exit_on_error: Determines whether or not the internal parser exits with error info when an error occurs.
            Defaults to `True`.
        _cli_prefix: The root parser command line arguments prefix. Defaults to "".
        _cli_flag_prefix_char: The flag prefix character to use for CLI optional arguments. Defaults to '-'.
        _cli_implicit_flags: Whether `bool` fields should be implicitly converted into CLI boolean flags.
            (e.g. --flag, --no-flag). Defaults to `False`.
        _cli_ignore_unknown_args: Whether to ignore unknown CLI args and parse only known ones. Defaults to `False`.
        _cli_kebab_case: CLI args use kebab case. Defaults to `False`.
        _cli_shortcuts: Mapping of target field name to alias names. Defaults to `None`.
        _secrets_dir: The secret files directory or a sequence of directories. Defaults to `None`.
    """

    def __init__(
        __pydantic_self__,
        _case_sensitive: bool | None = None,
        _nested_model_default_partial_update: bool | None = None,
        _env_prefix: str | None = None,
        _env_file: DotenvType | None = ENV_FILE_SENTINEL,
        _env_file_encoding: str | None = None,
        _env_ignore_empty: bool | None = None,
        _env_nested_delimiter: str | None = None,
        _env_nested_max_split: int | None = None,
        _env_parse_none_str: str | None = None,
        _env_parse_enums: bool | None = None,
        _cli_prog_name: str | None = None,
        _cli_parse_args: bool | list[str] | tuple[str, ...] | None = None,
        _cli_settings_source: CliSettingsSource[Any] | None = None,
        _cli_parse_none_str: str | None = None,
        _cli_hide_none_type: bool | None = None,
        _cli_avoid_json: bool | None = None,
        _cli_enforce_required: bool | None = None,
        _cli_use_class_docs_for_groups: bool | None = None,
        _cli_exit_on_error: bool | None = None,
        _cli_prefix: str | None = None,
        _cli_flag_prefix_char: str | None = None,
        _cli_implicit_flags: bool | None = None,
        _cli_ignore_unknown_args: bool | None = None,
        _cli_kebab_case: bool | None = None,
        _cli_shortcuts: Mapping[str, str | list[str]] | None = None,
        _secrets_dir: PathType | None = None,
        **values: Any,
    ) -> None:
        super().__init__(
            **__pydantic_self__._settings_build_values(
                values,
                _case_sensitive=_case_sensitive,
                _nested_model_default_partial_update=_nested_model_default_partial_update,
                _env_prefix=_env_prefix,
                _env_file=_env_file,
                _env_file_encoding=_env_file_encoding,
                _env_ignore_empty=_env_ignore_empty,
                _env_nested_delimiter=_env_nested_delimiter,
                _env_nested_max_split=_env_nested_max_split,
                _env_parse_none_str=_env_parse_none_str,
                _env_parse_enums=_env_parse_enums,
                _cli_prog_name=_cli_prog_name,
                _cli_parse_args=_cli_parse_args,
                _cli_settings_source=_cli_settings_source,
                _cli_parse_none_str=_cli_parse_none_str,
                _cli_hide_none_type=_cli_hide_none_type,
                _cli_avoid_json=_cli_avoid_json,
                _cli_enforce_required=_cli_enforce_required,
                _cli_use_class_docs_for_groups=_cli_use_class_docs_for_groups,
                _cli_exit_on_error=_cli_exit_on_error,
                _cli_prefix=_cli_prefix,
                _cli_flag_prefix_char=_cli_flag_prefix_char,
                _cli_implicit_flags=_cli_implicit_flags,
                _cli_ignore_unknown_args=_cli_ignore_unknown_args,
                _cli_kebab_case=_cli_kebab_case,
                _cli_shortcuts=_cli_shortcuts,
                _secrets_dir=_secrets_dir,
            )
        )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """
        Define the sources and their order for loading the settings values.

        Args:
            settings_cls: The Settings class.
            init_settings: The `InitSettingsSource` instance.
            env_settings: The `EnvSettingsSource` instance.
            dotenv_settings: The `DotEnvSettingsSource` instance.
            file_secret_settings: The `SecretsSettingsSource` instance.

        Returns:
            A tuple containing the sources and their order for loading the settings values.
        """
        assert isinstance(env_settings, EnvSettingsSource)
        assert isinstance(dotenv_settings, DotEnvSettingsSource)
        return (
            init_settings,
            env_settings,
            type(env_settings)(
                settings_cls=env_settings.settings_cls,
                case_sensitive=env_settings.case_sensitive,
                env_prefix="",
                env_nested_delimiter=env_settings.env_nested_delimiter,
                env_nested_max_split=env_settings.env_nested_max_split,
                env_ignore_empty=env_settings.env_ignore_empty,
                env_parse_none_str=env_settings.env_parse_none_str,
                env_parse_enums=env_settings.env_parse_enums,
            ),
            dotenv_settings,
            type(dotenv_settings)(
                settings_cls=dotenv_settings.settings_cls,
                env_file=dotenv_settings.env_file,
                env_file_encoding=dotenv_settings.env_file_encoding,
                case_sensitive=dotenv_settings.case_sensitive,
                env_prefix="",
                env_nested_delimiter=dotenv_settings.env_nested_delimiter,
                env_nested_max_split=dotenv_settings.env_nested_max_split,
                env_ignore_empty=dotenv_settings.env_ignore_empty,
                env_parse_none_str=dotenv_settings.env_parse_none_str,
                env_parse_enums=dotenv_settings.env_parse_enums,
            ),
            file_secret_settings,
        )

    def _settings_build_values(
        self,
        init_kwargs: dict[str, Any],
        _case_sensitive: bool | None = None,
        _nested_model_default_partial_update: bool | None = None,
        _env_prefix: str | None = None,
        _env_file: DotenvType | None = None,
        _env_file_encoding: str | None = None,
        _env_ignore_empty: bool | None = None,
        _env_nested_delimiter: str | None = None,
        _env_nested_max_split: int | None = None,
        _env_parse_none_str: str | None = None,
        _env_parse_enums: bool | None = None,
        _cli_prog_name: str | None = None,
        _cli_parse_args: bool | list[str] | tuple[str, ...] | None = None,
        _cli_settings_source: CliSettingsSource[Any] | None = None,
        _cli_parse_none_str: str | None = None,
        _cli_hide_none_type: bool | None = None,
        _cli_avoid_json: bool | None = None,
        _cli_enforce_required: bool | None = None,
        _cli_use_class_docs_for_groups: bool | None = None,
        _cli_exit_on_error: bool | None = None,
        _cli_prefix: str | None = None,
        _cli_flag_prefix_char: str | None = None,
        _cli_implicit_flags: bool | None = None,
        _cli_ignore_unknown_args: bool | None = None,
        _cli_kebab_case: bool | None = None,
        _cli_shortcuts: Mapping[str, str | list[str]] | None = None,
        _secrets_dir: PathType | None = None,
    ) -> dict[str, Any]:
        # Determine settings config values
        case_sensitive = _case_sensitive if _case_sensitive is not None else self.model_config.get('case_sensitive')
        env_prefix = _env_prefix if _env_prefix is not None else self.model_config.get('env_prefix')
        nested_model_default_partial_update = (
            _nested_model_default_partial_update
            if _nested_model_default_partial_update is not None
            else self.model_config.get('nested_model_default_partial_update')
        )
        env_file = _env_file if _env_file != ENV_FILE_SENTINEL else self.model_config.get('env_file')
        env_file_encoding = (
            _env_file_encoding if _env_file_encoding is not None else self.model_config.get('env_file_encoding')
        )
        env_ignore_empty = (
            _env_ignore_empty if _env_ignore_empty is not None else self.model_config.get('env_ignore_empty')
        )
        env_nested_delimiter = (
            _env_nested_delimiter
            if _env_nested_delimiter is not None
            else self.model_config.get('env_nested_delimiter')
        )
        env_nested_max_split = (
            _env_nested_max_split
            if _env_nested_max_split is not None
            else self.model_config.get('env_nested_max_split')
        )
        env_parse_none_str = (
            _env_parse_none_str if _env_parse_none_str is not None else self.model_config.get('env_parse_none_str')
        )
        env_parse_enums = _env_parse_enums if _env_parse_enums is not None else self.model_config.get('env_parse_enums')

        cli_prog_name = _cli_prog_name if _cli_prog_name is not None else self.model_config.get('cli_prog_name')
        cli_parse_args = _cli_parse_args if _cli_parse_args is not None else self.model_config.get('cli_parse_args')
        cli_settings_source = (
            _cli_settings_source if _cli_settings_source is not None else self.model_config.get('cli_settings_source')
        )
        cli_parse_none_str = (
            _cli_parse_none_str if _cli_parse_none_str is not None else self.model_config.get('cli_parse_none_str')
        )
        cli_parse_none_str = cli_parse_none_str if not env_parse_none_str else env_parse_none_str
        cli_hide_none_type = (
            _cli_hide_none_type if _cli_hide_none_type is not None else self.model_config.get('cli_hide_none_type')
        )
        cli_avoid_json = _cli_avoid_json if _cli_avoid_json is not None else self.model_config.get('cli_avoid_json')
        cli_enforce_required = (
            _cli_enforce_required
            if _cli_enforce_required is not None
            else self.model_config.get('cli_enforce_required')
        )
        cli_use_class_docs_for_groups = (
            _cli_use_class_docs_for_groups
            if _cli_use_class_docs_for_groups is not None
            else self.model_config.get('cli_use_class_docs_for_groups')
        )
        cli_exit_on_error = (
            _cli_exit_on_error if _cli_exit_on_error is not None else self.model_config.get('cli_exit_on_error')
        )
        cli_prefix = _cli_prefix if _cli_prefix is not None else self.model_config.get('cli_prefix')
        cli_flag_prefix_char = (
            _cli_flag_prefix_char
            if _cli_flag_prefix_char is not None
            else self.model_config.get('cli_flag_prefix_char')
        )
        cli_implicit_flags = (
            _cli_implicit_flags if _cli_implicit_flags is not None else self.model_config.get('cli_implicit_flags')
        )
        cli_ignore_unknown_args = (
            _cli_ignore_unknown_args
            if _cli_ignore_unknown_args is not None
            else self.model_config.get('cli_ignore_unknown_args')
        )
        cli_kebab_case = _cli_kebab_case if _cli_kebab_case is not None else self.model_config.get('cli_kebab_case')
        cli_shortcuts = _cli_shortcuts if _cli_shortcuts is not None else self.model_config.get('cli_shortcuts')

        secrets_dir = _secrets_dir if _secrets_dir is not None else self.model_config.get('secrets_dir')

        # Configure built-in sources
        default_settings = DefaultSettingsSource(
            self.__class__, nested_model_default_partial_update=nested_model_default_partial_update
        )
        init_settings = InitSettingsSource(
            self.__class__,
            init_kwargs=init_kwargs,
            nested_model_default_partial_update=nested_model_default_partial_update,
        )
        env_settings = EnvSettingsSource(
            self.__class__,
            case_sensitive=case_sensitive,
            env_prefix=env_prefix,
            env_nested_delimiter=env_nested_delimiter,
            env_nested_max_split=env_nested_max_split,
            env_ignore_empty=env_ignore_empty,
            env_parse_none_str=env_parse_none_str,
            env_parse_enums=env_parse_enums,
        )
        dotenv_settings = DotEnvSettingsSource(
            self.__class__,
            env_file=env_file,
            env_file_encoding=env_file_encoding,
            case_sensitive=case_sensitive,
            env_prefix=env_prefix,
            env_nested_delimiter=env_nested_delimiter,
            env_nested_max_split=env_nested_max_split,
            env_ignore_empty=env_ignore_empty,
            env_parse_none_str=env_parse_none_str,
            env_parse_enums=env_parse_enums,
        )

        file_secret_settings = SecretsSettingsSource(
            self.__class__, secrets_dir=secrets_dir, case_sensitive=case_sensitive, env_prefix=env_prefix
        )
        # Provide a hook to set built-in sources priority and add / remove sources
        sources = self.settings_customise_sources(
            self.__class__,
            init_settings=init_settings,
            env_settings=env_settings,
            dotenv_settings=dotenv_settings,
            file_secret_settings=file_secret_settings,
        ) + (default_settings,)
        custom_cli_sources = [source for source in sources if isinstance(source, CliSettingsSource)]
        if not any(custom_cli_sources):
            if isinstance(cli_settings_source, CliSettingsSource):
                sources = (cli_settings_source,) + sources
            elif cli_parse_args is not None:
                cli_settings = CliSettingsSource[Any](
                    self.__class__,
                    cli_prog_name=cli_prog_name,
                    cli_parse_args=cli_parse_args,
                    cli_parse_none_str=cli_parse_none_str,
                    cli_hide_none_type=cli_hide_none_type,
                    cli_avoid_json=cli_avoid_json,
                    cli_enforce_required=cli_enforce_required,
                    cli_use_class_docs_for_groups=cli_use_class_docs_for_groups,
                    cli_exit_on_error=cli_exit_on_error,
                    cli_prefix=cli_prefix,
                    cli_flag_prefix_char=cli_flag_prefix_char,
                    cli_implicit_flags=cli_implicit_flags,
                    cli_ignore_unknown_args=cli_ignore_unknown_args,
                    cli_kebab_case=cli_kebab_case,
                    cli_shortcuts=cli_shortcuts,
                    case_sensitive=case_sensitive,
                )
                sources = (cli_settings,) + sources
        # We ensure that if command line arguments haven't been parsed yet, we do so.
        elif cli_parse_args not in (None, False) and not custom_cli_sources[0].env_vars:
            custom_cli_sources[0](args=cli_parse_args)  # type: ignore

        if sources:
            state: dict[str, Any] = {}
            states: dict[str, dict[str, Any]] = {}
            for source in sources:
                if isinstance(source, PydanticBaseSettingsSource):
                    source._set_current_state(state)
                    source._set_settings_sources_data(states)

                source_name = source.__name__ if hasattr(source, '__name__') else type(source).__name__
                source_state = source()

                states[source_name] = source_state
                state = deep_update(source_state, state)
            return state
        else:
            # no one should mean to do this, but I think returning an empty dict is marginally preferable
            # to an informative error and much better than a confusing error
            return {}

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        extra='forbid',
        arbitrary_types_allowed=True,
        validate_default=True,
        case_sensitive=False,
        env_prefix='',
        nested_model_default_partial_update=False,
        env_file=None,
        env_file_encoding=None,
        env_ignore_empty=False,
        env_nested_delimiter=None,
        env_nested_max_split=None,
        env_parse_none_str=None,
        env_parse_enums=None,
        cli_prog_name=None,
        cli_parse_args=None,
        cli_parse_none_str=None,
        cli_hide_none_type=False,
        cli_avoid_json=False,
        cli_enforce_required=False,
        cli_use_class_docs_for_groups=False,
        cli_exit_on_error=True,
        cli_prefix='',
        cli_flag_prefix_char='-',
        cli_implicit_flags=False,
        cli_ignore_unknown_args=False,
        cli_kebab_case=False,
        cli_shortcuts=None,
        json_file=None,
        json_file_encoding=None,
        yaml_file=None,
        yaml_file_encoding=None,
        yaml_config_section=None,
        toml_file=None,
        secrets_dir=None,
        protected_namespaces=('model_validate', 'model_dump', 'settings_customise_sources'),
        enable_decoding=True,
    )


class CliApp:
    """
    A utility class for running Pydantic `BaseSettings`, `BaseModel`, or `pydantic.dataclasses.dataclass` as
    CLI applications.
    """

    @staticmethod
    def _get_base_settings_cls(model_cls: type[Any]) -> type[BaseSettings]:
        if issubclass(model_cls, BaseSettings):
            return model_cls

        class CliAppBaseSettings(BaseSettings, model_cls):  # type: ignore
            __doc__ = model_cls.__doc__
            model_config = SettingsConfigDict(
                nested_model_default_partial_update=True,
                case_sensitive=True,
                cli_hide_none_type=True,
                cli_avoid_json=True,
                cli_enforce_required=True,
                cli_implicit_flags=True,
                cli_kebab_case=True,
            )

        return CliAppBaseSettings

    @staticmethod
    def _run_cli_cmd(model: Any, cli_cmd_method_name: str, is_required: bool) -> Any:
        command = getattr(type(model), cli_cmd_method_name, None)
        if command is None:
            if is_required:
                raise SettingsError(f'Error: {type(model).__name__} class is missing {cli_cmd_method_name} entrypoint')
            return model

        # If the method is asynchronous, we handle its execution based on the current event loop status.
        if inspect.iscoroutinefunction(command):
            # For asynchronous methods, we have two execution scenarios:
            # 1. If no event loop is running in the current thread, run the coroutine directly with asyncio.run().
            # 2. If an event loop is already running in the current thread, run the coroutine in a separate thread to avoid conflicts.
            try:
                # Check if an event loop is currently running in this thread.
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # We're in a context with an active event loop (e.g., Jupyter Notebook).
                # Running asyncio.run() here would cause conflicts, so we use a separate thread.
                exception_container = []

                def run_coro() -> None:
                    try:
                        # Execute the coroutine in a new event loop in this separate thread.
                        asyncio.run(command(model))
                    except Exception as e:
                        exception_container.append(e)

                thread = threading.Thread(target=run_coro)
                thread.start()
                thread.join()
                if exception_container:
                    # Propagate exceptions from the separate thread.
                    raise exception_container[0]
            else:
                # No event loop is running; safe to run the coroutine directly.
                asyncio.run(command(model))
        else:
            # For synchronous methods, call them directly.
            command(model)

        return model

    @staticmethod
    def run(
        model_cls: type[T],
        cli_args: list[str] | Namespace | SimpleNamespace | dict[str, Any] | None = None,
        cli_settings_source: CliSettingsSource[Any] | None = None,
        cli_exit_on_error: bool | None = None,
        cli_cmd_method_name: str = 'cli_cmd',
        **model_init_data: Any,
    ) -> T:
        """
        Runs a Pydantic `BaseSettings`, `BaseModel`, or `pydantic.dataclasses.dataclass` as a CLI application.
        Running a model as a CLI application requires the `cli_cmd` method to be defined in the model class.

        Args:
            model_cls: The model class to run as a CLI application.
            cli_args: The list of CLI arguments to parse. If `cli_settings_source` is specified, this may
                also be a namespace or dictionary of pre-parsed CLI arguments. Defaults to `sys.argv[1:]`.
            cli_settings_source: Override the default CLI settings source with a user defined instance.
                Defaults to `None`.
            cli_exit_on_error: Determines whether this function exits on error. If model is subclass of
                `BaseSettings`, defaults to BaseSettings `cli_exit_on_error` value. Otherwise, defaults to
                `True`.
            cli_cmd_method_name: The CLI command method name to run. Defaults to "cli_cmd".
            model_init_data: The model init data.

        Returns:
            The ran instance of model.

        Raises:
            SettingsError: If model_cls is not subclass of `BaseModel` or `pydantic.dataclasses.dataclass`.
            SettingsError: If model_cls does not have a `cli_cmd` entrypoint defined.
        """

        if not (is_pydantic_dataclass(model_cls) or is_model_class(model_cls)):
            raise SettingsError(
                f'Error: {model_cls.__name__} is not subclass of BaseModel or pydantic.dataclasses.dataclass'
            )

        cli_settings = None
        cli_parse_args = True if cli_args is None else cli_args
        if cli_settings_source is not None:
            if isinstance(cli_parse_args, (Namespace, SimpleNamespace, dict)):
                cli_settings = cli_settings_source(parsed_args=cli_parse_args)
            else:
                cli_settings = cli_settings_source(args=cli_parse_args)
        elif isinstance(cli_parse_args, (Namespace, SimpleNamespace, dict)):
            raise SettingsError('Error: `cli_args` must be list[str] or None when `cli_settings_source` is not used')

        model_init_data['_cli_parse_args'] = cli_parse_args
        model_init_data['_cli_exit_on_error'] = cli_exit_on_error
        model_init_data['_cli_settings_source'] = cli_settings
        if not issubclass(model_cls, BaseSettings):
            base_settings_cls = CliApp._get_base_settings_cls(model_cls)
            model = base_settings_cls(**model_init_data)
            model_init_data = {}
            for field_name, field_info in base_settings_cls.model_fields.items():
                model_init_data[_field_name_for_signature(field_name, field_info)] = getattr(model, field_name)

        return CliApp._run_cli_cmd(model_cls(**model_init_data), cli_cmd_method_name, is_required=False)

    @staticmethod
    def run_subcommand(
        model: PydanticModel, cli_exit_on_error: bool | None = None, cli_cmd_method_name: str = 'cli_cmd'
    ) -> PydanticModel:
        """
        Runs the model subcommand. Running a model subcommand requires the `cli_cmd` method to be defined in
        the nested model subcommand class.

        Args:
            model: The model to run the subcommand from.
            cli_exit_on_error: Determines whether this function exits with error if no subcommand is found.
                Defaults to model_config `cli_exit_on_error` value if set. Otherwise, defaults to `True`.
            cli_cmd_method_name: The CLI command method name to run. Defaults to "cli_cmd".

        Returns:
            The ran subcommand model.

        Raises:
            SystemExit: When no subcommand is found and cli_exit_on_error=`True` (the default).
            SettingsError: When no subcommand is found and cli_exit_on_error=`False`.
        """

        subcommand = get_subcommand(model, is_required=True, cli_exit_on_error=cli_exit_on_error)
        return CliApp._run_cli_cmd(subcommand, cli_cmd_method_name, is_required=True)

    @staticmethod
    def serialize(model: PydanticModel) -> list[str]:
        """
        Serializes the CLI arguments for a Pydantic data model.

        Args:
            model: The data model to serialize.

        Returns:
            The serialized CLI arguments for the data model.
        """

        base_settings_cls = CliApp._get_base_settings_cls(type(model))
        return CliSettingsSource._serialized_args(model, base_settings_cls.model_config)
