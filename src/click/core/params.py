from __future__ import annotations

import collections.abc as cabc
import inspect
import os
import types
import typing as t
from gettext import gettext as _
from gettext import ngettext

from click.core.utils import _check_iter
from click.core.utils import batch
from click.exceptions import BadParameter
from click.exceptions import MissingParameter
from click.formatting import join_options
from click.parser import _flag_needs_value
from click.parser import _split_opt
from click.termui import confirm
from click.termui import prompt

if t.TYPE_CHECKING:
    from click.core.context import Context
    from click.core.parameter_source import ParameterSource
    from click.parser import _OptionParser
    from click.shell_completion import CompletionItem

__all__ = [
    "Argument",
    "Parameter",
    "Option",
]


class Parameter:
    r"""A parameter to a command comes in two versions: they are either
    :class:`Option`\s or :class:`Argument`\s.  Other subclasses are currently
    not supported by design as some of the internals for parsing are
    intentionally not finalized.

    Some settings are supported by both options and arguments.

    :param param_decls: the parameter declarations for this option or
                        argument.  This is a list of flags or argument
                        names.
    :param type: the type that should be used.  Either a :class:`ParamType`
                 or a Python type.  The latter is converted into the former
                 automatically if supported.
    :param required: controls if this is optional or not.
    :param default: the default value if omitted.  This can also be a callable,
                    in which case it's invoked when the default is needed
                    without any arguments.
    :param callback: A function to further process or validate the value
        after type conversion. It is called as ``f(ctx, param, value)``
        and must return the value. It is called for all sources,
        including prompts.
    :param nargs: the number of arguments to match.  If not ``1`` the return
                  value is a tuple instead of single value.  The default for
                  nargs is ``1`` (except if the type is a tuple, then it's
                  the arity of the tuple). If ``nargs=-1``, all remaining
                  parameters are collected.
    :param metavar: how the value is represented in the help page.
    :param expose_value: if this is `True` then the value is passed onwards
                         to the command callback and stored on the context,
                         otherwise it's skipped.
    :param is_eager: eager values are processed before non eager ones.  This
                     should not be set for arguments or it will inverse the
                     order of processing.
    :param envvar: a string or list of strings that are environment variables
                   that should be checked.
    :param shell_complete: A function that returns custom shell
        completions. Used instead of the param's type completion if
        given. Takes ``ctx, param, incomplete`` and must return a list
        of :class:`~click.shell_completion.CompletionItem` or a list of
        strings.

    .. versionchanged:: 8.0
        ``process_value`` validates required parameters and bounded
        ``nargs``, and invokes the parameter callback before returning
        the value. This allows the callback to validate prompts.
        ``full_process_value`` is removed.

    .. versionchanged:: 8.0
        ``autocompletion`` is renamed to ``shell_complete`` and has new
        semantics described above. The old name is deprecated and will
        be removed in 8.1, until then it will be wrapped to match the
        new requirements.

    .. versionchanged:: 8.0
        For ``multiple=True, nargs>1``, the default must be a list of
        tuples.

    .. versionchanged:: 8.0
        Setting a default is no longer required for ``nargs>1``, it will
        default to ``None``. ``multiple=True`` or ``nargs=-1`` will
        default to ``()``.

    .. versionchanged:: 7.1
        Empty environment variables are ignored rather than taking the
        empty string value. This makes it possible for scripts to clear
        variables if they can't unset them.

    .. versionchanged:: 2.0
        Changed signature for parameter callback to also be passed the
        parameter. The old callback format will still work, but it will
        raise a warning to give you a chance to migrate the code easier.
    """

    param_type_name = "parameter"

    def __init__(
        self,
        param_decls: cabc.Sequence[str] | None = None,
        type: types.ParamType | t.Any | None = None,
        required: bool = False,
        default: t.Any | t.Callable[[], t.Any] | None = None,
        callback: t.Callable[[Context, Parameter, t.Any], t.Any] | None = None,
        nargs: int | None = None,
        multiple: bool = False,
        metavar: str | None = None,
        expose_value: bool = True,
        is_eager: bool = False,
        envvar: str | cabc.Sequence[str] | None = None,
        shell_complete: t.Callable[
            [Context, Parameter, str], list[CompletionItem] | list[str]
        ]
        | None = None,
    ) -> None:
        self.name: str | None
        self.opts: list[str]
        self.secondary_opts: list[str]
        self.name, self.opts, self.secondary_opts = self._parse_decls(
            param_decls or (), expose_value
        )
        self.type: types.ParamType = types.convert_type(type, default)

        # Default nargs to what the type tells us if we have that
        # information available.
        if nargs is None:
            if self.type.is_composite:
                nargs = self.type.arity
            else:
                nargs = 1

        self.required = required
        self.callback = callback
        self.nargs = nargs
        self.multiple = multiple
        self.expose_value = expose_value
        self.default = default
        self.is_eager = is_eager
        self.metavar = metavar
        self.envvar = envvar
        self._custom_shell_complete = shell_complete

        if __debug__:
            if self.type.is_composite and nargs != self.type.arity:
                raise ValueError(
                    f"'nargs' must be {self.type.arity} (or None) for"
                    f" type {self.type!r}, but it was {nargs}."
                )

            # Skip no default or callable default.
            check_default = default if not callable(default) else None

            if check_default is not None:
                if multiple:
                    try:
                        # Only check the first value against nargs.
                        check_default = next(_check_iter(check_default), None)
                    except TypeError:
                        raise ValueError(
                            "'default' must be a list when 'multiple' is true."
                        ) from None

                # Can be None for multiple with empty default.
                if nargs != 1 and check_default is not None:
                    try:
                        _check_iter(check_default)
                    except TypeError:
                        if multiple:
                            message = (
                                "'default' must be a list of lists when 'multiple' is"
                                " true and 'nargs' != 1."
                            )
                        else:
                            message = "'default' must be a list when 'nargs' != 1."

                        raise ValueError(message) from None

                    if nargs > 1 and len(check_default) != nargs:
                        subject = "item length" if multiple else "length"
                        raise ValueError(
                            f"'default' {subject} must match nargs={nargs}."
                        )

    def to_info_dict(self) -> dict[str, t.Any]:
        """Gather information that could be useful for a tool generating
        user-facing documentation.

        Use :meth:`click.Context.to_info_dict` to traverse the entire
        CLI structure.

        .. versionadded:: 8.0
        """
        return {
            "name": self.name,
            "param_type_name": self.param_type_name,
            "opts": self.opts,
            "secondary_opts": self.secondary_opts,
            "type": self.type.to_info_dict(),
            "required": self.required,
            "nargs": self.nargs,
            "multiple": self.multiple,
            "default": self.default,
            "envvar": self.envvar,
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self.name}>"

    def _parse_decls(
        self, decls: cabc.Sequence[str], expose_value: bool
    ) -> tuple[str | None, list[str], list[str]]:
        raise NotImplementedError()

    @property
    def human_readable_name(self) -> str:
        """Returns the human readable name of this parameter.  This is the
        same as the name for options, but the metavar for arguments.
        """
        return self.name  # type: ignore

    def make_metavar(self) -> str:
        if self.metavar is not None:
            return self.metavar

        metavar = self.type.get_metavar(self)

        if metavar is None:
            metavar = self.type.name.upper()

        if self.nargs != 1:
            metavar += "..."

        return metavar

    @t.overload
    def get_default(
        self, ctx: Context, call: t.Literal[True] = True
    ) -> t.Any | None: ...

    @t.overload
    def get_default(
        self, ctx: Context, call: bool = ...
    ) -> t.Any | t.Callable[[], t.Any] | None: ...

    def get_default(
        self, ctx: Context, call: bool = True
    ) -> t.Any | t.Callable[[], t.Any] | None:
        """Get the default for the parameter. Tries
        :meth:`Context.lookup_default` first, then the local default.

        :param ctx: Current context.
        :param call: If the default is a callable, call it. Disable to
            return the callable instead.

        .. versionchanged:: 8.0.2
            Type casting is no longer performed when getting a default.

        .. versionchanged:: 8.0.1
            Type casting can fail in resilient parsing mode. Invalid
            defaults will not prevent showing help text.

        .. versionchanged:: 8.0
            Looks at ``ctx.default_map`` first.

        .. versionchanged:: 8.0
            Added the ``call`` parameter.
        """
        value = ctx.lookup_default(self.name, call=False)  # type: ignore

        if value is None:
            value = self.default

        if call and callable(value):
            value = value()

        return value

    def add_to_parser(self, parser: _OptionParser, ctx: Context) -> None:
        raise NotImplementedError()

    def consume_value(
        self, ctx: Context, opts: cabc.Mapping[str, t.Any]
    ) -> tuple[t.Any, ParameterSource]:
        from click.core.parameter_source import ParameterSource

        value = opts.get(self.name)  # type: ignore
        source = ParameterSource.COMMANDLINE

        if value is None:
            value = self.value_from_envvar(ctx)
            source = ParameterSource.ENVIRONMENT

        if value is None:
            value = ctx.lookup_default(self.name)  # type: ignore
            source = ParameterSource.DEFAULT_MAP

        if value is None:
            value = self.get_default(ctx)
            source = ParameterSource.DEFAULT

        return value, source

    def type_cast_value(self, ctx: Context, value: t.Any) -> t.Any:
        """Convert and validate a value against the option's
        :attr:`type`, :attr:`multiple`, and :attr:`nargs`.
        """
        if value is None:
            return () if self.multiple or self.nargs == -1 else None

        def check_iter(value: t.Any) -> cabc.Iterator[t.Any]:
            try:
                return _check_iter(value)
            except TypeError:
                # This should only happen when passing in args manually,
                # the parser should construct an iterable when parsing
                # the command line.
                raise BadParameter(
                    _("Value must be an iterable."), ctx=ctx, param=self
                ) from None

        if self.nargs == 1 or self.type.is_composite:

            def convert(value: t.Any) -> t.Any:
                return self.type(value, param=self, ctx=ctx)

        elif self.nargs == -1:

            def convert(value: t.Any) -> t.Any:  # tuple[t.Any, ...]
                return tuple(self.type(x, self, ctx) for x in check_iter(value))

        else:  # nargs > 1

            def convert(value: t.Any) -> t.Any:  # tuple[t.Any, ...]
                value = tuple(check_iter(value))

                if len(value) != self.nargs:
                    raise BadParameter(
                        ngettext(
                            "Takes {nargs} values but 1 was given.",
                            "Takes {nargs} values but {len} were given.",
                            len(value),
                        ).format(nargs=self.nargs, len=len(value)),
                        ctx=ctx,
                        param=self,
                    )

                return tuple(self.type(x, self, ctx) for x in value)

        if self.multiple:
            return tuple(convert(x) for x in check_iter(value))

        return convert(value)

    def value_is_missing(self, value: t.Any) -> bool:
        if value is None:
            return True

        if (self.nargs != 1 or self.multiple) and value == ():
            return True

        return False

    def process_value(self, ctx: Context, value: t.Any) -> t.Any:
        value = self.type_cast_value(ctx, value)

        if self.required and self.value_is_missing(value):
            raise MissingParameter(ctx=ctx, param=self)

        if self.callback is not None:
            value = self.callback(ctx, self, value)

        return value

    def resolve_envvar_value(self, ctx: Context) -> str | None:
        if self.envvar is None:
            return None

        if isinstance(self.envvar, str):
            rv = os.environ.get(self.envvar)

            if rv:
                return rv
        else:
            for envvar in self.envvar:
                rv = os.environ.get(envvar)

                if rv:
                    return rv

        return None

    def value_from_envvar(self, ctx: Context) -> t.Any | None:
        rv: t.Any | None = self.resolve_envvar_value(ctx)

        if rv is not None and self.nargs != 1:
            rv = self.type.split_envvar_value(rv)

        return rv

    def handle_parse_result(
        self, ctx: Context, opts: cabc.Mapping[str, t.Any], args: list[str]
    ) -> tuple[t.Any, list[str]]:
        from click.core.utils import augment_usage_errors

        with augment_usage_errors(ctx, param=self):
            value, source = self.consume_value(ctx, opts)
            ctx.set_parameter_source(self.name, source)  # type: ignore

            try:
                value = self.process_value(ctx, value)
            except Exception:
                if not ctx.resilient_parsing:
                    raise

                value = None

        if self.expose_value:
            ctx.params[self.name] = value  # type: ignore

        return value, args

    def get_help_record(self, ctx: Context) -> tuple[str, str] | None:
        pass

    def get_usage_pieces(self, ctx: Context) -> list[str]:
        return []

    def get_error_hint(self, ctx: Context) -> str:
        """Get a stringified version of the param for use in error messages to
        indicate which param caused the error.
        """
        hint_list = self.opts or [self.human_readable_name]
        return " / ".join(f"'{x}'" for x in hint_list)

    def shell_complete(self, ctx: Context, incomplete: str) -> list[CompletionItem]:
        """Return a list of completions for the incomplete value. If a
        ``shell_complete`` function was given during init, it is used.
        Otherwise, the :attr:`type`
        :meth:`~click.types.ParamType.shell_complete` function is used.

        :param ctx: Invocation context for this command.
        :param incomplete: Value being completed. May be empty.

        .. versionadded:: 8.0
        """
        if self._custom_shell_complete is not None:
            results = self._custom_shell_complete(ctx, self, incomplete)

            if results and isinstance(results[0], str):
                from click.shell_completion import CompletionItem

                results = [CompletionItem(c) for c in results]

            return t.cast("list[CompletionItem]", results)

        return self.type.shell_complete(ctx, self, incomplete)


class Option(Parameter):
    """Options are usually optional values on the command line and
    have some extra features that arguments don't have.

    All other parameters are passed onwards to the parameter constructor.

    :param show_default: Show the default value for this option in its
        help text. Values are not shown by default, unless
        :attr:`Context.show_default` is ``True``. If this value is a
        string, it shows that string in parentheses instead of the
        actual value. This is particularly useful for dynamic options.
        For single option boolean flags, the default remains hidden if
        its value is ``False``.
    :param show_envvar: Controls if an environment variable should be
        shown on the help page. Normally, environment variables are not
        shown.
    :param prompt: If set to ``True`` or a non empty string then the
        user will be prompted for input. If set to ``True`` the prompt
        will be the option name capitalized.
    :param confirmation_prompt: Prompt a second time to confirm the
        value if it was prompted for. Can be set to a string instead of
        ``True`` to customize the message.
    :param prompt_required: If set to ``False``, the user will be
        prompted for input only when the option was specified as a flag
        without a value.
    :param hide_input: If this is ``True`` then the input on the prompt
        will be hidden from the user. This is useful for password input.
    :param is_flag: forces this option to act as a flag.  The default is
                    auto detection.
    :param flag_value: which value should be used for this flag if it's
                       enabled.  This is set to a boolean automatically if
                       the option string contains a slash to mark two options.
    :param multiple: if this is set to `True` then the argument is accepted
                     multiple times and recorded.  This is similar to ``nargs``
                     in how it works but supports arbitrary number of
                     arguments.
    :param count: this flag makes an option increment an integer.
    :param allow_from_autoenv: if this is enabled then the value of this
                               parameter will be pulled from an environment
                               variable in case a prefix is defined on the
                               context.
    :param help: the help string.
    :param hidden: hide this option from help outputs.
    :param attrs: Other command arguments described in :class:`Parameter`.

    .. versionchanged:: 8.1.0
        Help text indentation is cleaned here instead of only in the
        ``@option`` decorator.

    .. versionchanged:: 8.1.0
        The ``show_default`` parameter overrides
        ``Context.show_default``.

    .. versionchanged:: 8.1.0
        The default of a single option boolean flag is not shown if the
        default value is ``False``.

    .. versionchanged:: 8.0.1
        ``type`` is detected from ``flag_value`` if given.
    """

    param_type_name = "option"

    def __init__(
        self,
        param_decls: cabc.Sequence[str] | None = None,
        show_default: bool | str | None = None,
        prompt: bool | str = False,
        confirmation_prompt: bool | str = False,
        prompt_required: bool = True,
        hide_input: bool = False,
        is_flag: bool | None = None,
        flag_value: t.Any | None = None,
        multiple: bool = False,
        count: bool = False,
        allow_from_autoenv: bool = True,
        type: types.ParamType | t.Any | None = None,
        help: str | None = None,
        hidden: bool = False,
        show_choices: bool = True,
        show_envvar: bool = False,
        **attrs: t.Any,
    ) -> None:
        if help:
            help = inspect.cleandoc(help)

        default_is_missing = "default" not in attrs
        super().__init__(param_decls, type=type, multiple=multiple, **attrs)

        if prompt is True:
            if self.name is None:
                raise TypeError("'name' is required with 'prompt=True'.")

            prompt_text: str | None = self.name.replace("_", " ").capitalize()
        elif prompt is False:
            prompt_text = None
        else:
            prompt_text = prompt

        self.prompt = prompt_text
        self.confirmation_prompt = confirmation_prompt
        self.prompt_required = prompt_required
        self.hide_input = hide_input
        self.hidden = hidden

        # If prompt is enabled but not required, then the option can be
        # used as a flag to indicate using prompt or flag_value.
        self._flag_needs_value = self.prompt is not None and not self.prompt_required

        if is_flag is None:
            if flag_value is not None:
                # Implicitly a flag because flag_value was set.
                is_flag = True
            elif self._flag_needs_value:
                # Not a flag, but when used as a flag it shows a prompt.
                is_flag = False
            else:
                # Implicitly a flag because flag options were given.
                is_flag = bool(self.secondary_opts)
        elif is_flag is False and not self._flag_needs_value:
            # Not a flag, and prompt is not enabled, can be used as a
            # flag if flag_value is set.
            self._flag_needs_value = flag_value is not None

        self.default: t.Any | t.Callable[[], t.Any]

        if is_flag and default_is_missing and not self.required:
            if multiple:
                self.default = ()
            else:
                self.default = False

        if flag_value is None:
            flag_value = not self.default

        self.type: types.ParamType
        if is_flag and type is None:
            # Re-guess the type from the flag value instead of the
            # default.
            self.type = types.convert_type(None, flag_value)

        self.is_flag: bool = is_flag
        self.is_bool_flag: bool = is_flag and isinstance(self.type, types.BoolParamType)
        self.flag_value: t.Any = flag_value

        # Counting
        self.count = count
        if count:
            if type is None:
                self.type = types.IntRange(min=0)
            if default_is_missing:
                self.default = 0

        self.allow_from_autoenv = allow_from_autoenv
        self.help = help
        self.show_default = show_default
        self.show_choices = show_choices
        self.show_envvar = show_envvar

        if __debug__:
            if self.nargs == -1:
                raise TypeError("nargs=-1 is not supported for options.")

            if self.prompt and self.is_flag and not self.is_bool_flag:
                raise TypeError("'prompt' is not valid for non-boolean flag.")

            if not self.is_bool_flag and self.secondary_opts:
                raise TypeError("Secondary flag is not valid for non-boolean flag.")

            if self.is_bool_flag and self.hide_input and self.prompt is not None:
                raise TypeError(
                    "'prompt' with 'hide_input' is not valid for boolean flag."
                )

            if self.count:
                if self.multiple:
                    raise TypeError("'count' is not valid with 'multiple'.")

                if self.is_flag:
                    raise TypeError("'count' is not valid with 'is_flag'.")

    def to_info_dict(self) -> dict[str, t.Any]:
        info_dict = super().to_info_dict()
        info_dict.update(
            help=self.help,
            prompt=self.prompt,
            is_flag=self.is_flag,
            flag_value=self.flag_value,
            count=self.count,
            hidden=self.hidden,
        )
        return info_dict

    def _parse_decls(
        self, decls: cabc.Sequence[str], expose_value: bool
    ) -> tuple[str | None, list[str], list[str]]:
        opts = []
        secondary_opts = []
        name = None
        possible_names = []

        for decl in decls:
            if decl.isidentifier():
                if name is not None:
                    raise TypeError(f"Name '{name}' defined twice")
                name = decl
            else:
                split_char = ";" if decl[:1] == "/" else "/"
                if split_char in decl:
                    first, second = decl.split(split_char, 1)
                    first = first.rstrip()
                    if first:
                        possible_names.append(_split_opt(first))
                        opts.append(first)
                    second = second.lstrip()
                    if second:
                        secondary_opts.append(second.lstrip())
                    if first == second:
                        raise ValueError(
                            f"Boolean option {decl!r} cannot use the"
                            " same flag for true/false."
                        )
                else:
                    possible_names.append(_split_opt(decl))
                    opts.append(decl)

        if name is None and possible_names:
            possible_names.sort(key=lambda x: -len(x[0]))  # group long options first
            name = possible_names[0][1].replace("-", "_").lower()
            if not name.isidentifier():
                name = None

        if name is None:
            if not expose_value:
                return None, opts, secondary_opts
            raise TypeError("Could not determine name for option")

        if not opts and not secondary_opts:
            raise TypeError(
                f"No options defined but a name was passed ({name})."
                " Did you mean to declare an argument instead? Did"
                f" you mean to pass '--{name}'?"
            )

        return name, opts, secondary_opts

    def add_to_parser(self, parser: _OptionParser, ctx: Context) -> None:
        if self.multiple:
            action = "append"
        elif self.count:
            action = "count"
        else:
            action = "store"

        if self.is_flag:
            action = f"{action}_const"

            if self.is_bool_flag and self.secondary_opts:
                parser.add_option(
                    obj=self, opts=self.opts, dest=self.name, action=action, const=True
                )
                parser.add_option(
                    obj=self,
                    opts=self.secondary_opts,
                    dest=self.name,
                    action=action,
                    const=False,
                )
            else:
                parser.add_option(
                    obj=self,
                    opts=self.opts,
                    dest=self.name,
                    action=action,
                    const=self.flag_value,
                )
        else:
            parser.add_option(
                obj=self,
                opts=self.opts,
                dest=self.name,
                action=action,
                nargs=self.nargs,
            )

    def get_help_record(self, ctx: Context) -> tuple[str, str] | None:
        if self.hidden:
            return None

        any_prefix_is_slash = False

        def _write_opts(opts: cabc.Sequence[str]) -> str:
            nonlocal any_prefix_is_slash

            rv, any_slashes = join_options(opts)

            if any_slashes:
                any_prefix_is_slash = True

            if not self.is_flag and not self.count:
                rv += f" {self.make_metavar()}"

            return rv

        rv = [_write_opts(self.opts)]

        if self.secondary_opts:
            rv.append(_write_opts(self.secondary_opts))

        help = self.help or ""
        extra = []

        if self.show_envvar:
            envvar = self.envvar

            if envvar is None:
                if (
                    self.allow_from_autoenv
                    and ctx.auto_envvar_prefix is not None
                    and self.name is not None
                ):
                    envvar = f"{ctx.auto_envvar_prefix}_{self.name.upper()}"

            if envvar is not None:
                var_str = (
                    envvar
                    if isinstance(envvar, str)
                    else ", ".join(str(d) for d in envvar)
                )
                extra.append(_("env var: {var}").format(var=var_str))

        # Temporarily enable resilient parsing to avoid type casting
        # failing for the default. Might be possible to extend this to
        # help formatting in general.
        resilient = ctx.resilient_parsing
        ctx.resilient_parsing = True

        try:
            default_value = self.get_default(ctx, call=False)
        finally:
            ctx.resilient_parsing = resilient

        show_default = False
        show_default_is_str = False

        if self.show_default is not None:
            if isinstance(self.show_default, str):
                show_default_is_str = show_default = True
            else:
                show_default = self.show_default
        elif ctx.show_default is not None:
            show_default = ctx.show_default

        if show_default_is_str or (show_default and (default_value is not None)):
            if show_default_is_str:
                default_string = f"({self.show_default})"
            elif isinstance(default_value, (list, tuple)):
                default_string = ", ".join(str(d) for d in default_value)
            elif inspect.isfunction(default_value):
                default_string = _("(dynamic)")
            elif self.is_bool_flag and self.secondary_opts:
                # For boolean flags that have distinct True/False opts,
                # use the opt without prefix instead of the value.
                default_string = _split_opt(
                    (self.opts if default_value else self.secondary_opts)[0]
                )[1]
            elif self.is_bool_flag and not self.secondary_opts and not default_value:
                default_string = ""
            elif default_value == "":
                default_string = '""'
            else:
                default_string = str(default_value)

            if default_string:
                extra.append(_("default: {default}").format(default=default_string))

        if (
            isinstance(self.type, types._NumberRangeBase)
            # skip count with default range type
            and not (self.count and self.type.min == 0 and self.type.max is None)
        ):
            range_str = self.type._describe_range()

            if range_str:
                extra.append(range_str)

        if self.required:
            extra.append(_("required"))

        if extra:
            extra_str = "; ".join(extra)
            help = f"{help}  [{extra_str}]" if help else f"[{extra_str}]"

        return ("; " if any_prefix_is_slash else " / ").join(rv), help

    @t.overload
    def get_default(
        self, ctx: Context, call: t.Literal[True] = True
    ) -> t.Any | None: ...

    @t.overload
    def get_default(
        self, ctx: Context, call: bool = ...
    ) -> t.Any | t.Callable[[], t.Any] | None: ...

    def get_default(
        self, ctx: Context, call: bool = True
    ) -> t.Any | t.Callable[[], t.Any] | None:
        # If we're a non boolean flag our default is more complex because
        # we need to look at all flags in the same group to figure out
        # if we're the default one in which case we return the flag
        # value as default.
        if self.is_flag and not self.is_bool_flag:
            for param in ctx.command.params:
                if param.name == self.name and param.default:
                    return t.cast(Option, param).flag_value

            return None

        return super().get_default(ctx, call=call)

    def prompt_for_value(self, ctx: Context) -> t.Any:
        """This is an alternative flow that can be activated in the full
        value processing if a value does not exist.  It will prompt the
        user until a valid value exists and then returns the processed
        value as result.
        """
        assert self.prompt is not None

        # Calculate the default before prompting anything to be stable.
        default = self.get_default(ctx)

        # If this is a prompt for a flag we need to handle this
        # differently.
        if self.is_bool_flag:
            return confirm(self.prompt, default)

        return prompt(
            self.prompt,
            default=default,
            type=self.type,
            hide_input=self.hide_input,
            show_choices=self.show_choices,
            confirmation_prompt=self.confirmation_prompt,
            value_proc=lambda x: self.process_value(ctx, x),
        )

    def resolve_envvar_value(self, ctx: Context) -> str | None:
        rv = super().resolve_envvar_value(ctx)

        if rv is not None:
            return rv

        if (
            self.allow_from_autoenv
            and ctx.auto_envvar_prefix is not None
            and self.name is not None
        ):
            envvar = f"{ctx.auto_envvar_prefix}_{self.name.upper()}"
            rv = os.environ.get(envvar)

            if rv:
                return rv

        return None

    def value_from_envvar(self, ctx: Context) -> t.Any | None:
        rv: t.Any | None = self.resolve_envvar_value(ctx)

        if rv is None:
            return None

        value_depth = (self.nargs != 1) + bool(self.multiple)

        if value_depth > 0:
            rv = self.type.split_envvar_value(rv)

            if self.multiple and self.nargs != 1:
                rv = batch(rv, self.nargs)

        return rv

    def consume_value(
        self, ctx: Context, opts: cabc.Mapping[str, Parameter]
    ) -> tuple[t.Any, ParameterSource]:
        value, source = super().consume_value(ctx, opts)

        # The parser will emit a sentinel value if the option can be
        # given as a flag without a value. This is different from None
        # to distinguish from the flag not being given at all.
        if value is _flag_needs_value:
            if self.prompt is not None and not ctx.resilient_parsing:
                value = self.prompt_for_value(ctx)
                source = ParameterSource.PROMPT
            else:
                value = self.flag_value
                source = ParameterSource.COMMANDLINE

        elif (
            self.multiple
            and value is not None
            and any(v is _flag_needs_value for v in value)
        ):
            value = [self.flag_value if v is _flag_needs_value else v for v in value]
            source = ParameterSource.COMMANDLINE

        # The value wasn't set, or used the param's default, prompt if
        # prompting is enabled.
        elif (
            source in {None, ParameterSource.DEFAULT}
            and self.prompt is not None
            and (self.required or self.prompt_required)
            and not ctx.resilient_parsing
        ):
            value = self.prompt_for_value(ctx)
            source = ParameterSource.PROMPT

        return value, source


class Argument(Parameter):
    """Arguments are positional parameters to a command.  They generally
    provide fewer features than options but can have infinite ``nargs``
    and are required by default.

    All parameters are passed onwards to the constructor of :class:`Parameter`.
    """

    param_type_name = "argument"

    def __init__(
        self,
        param_decls: cabc.Sequence[str],
        required: bool | None = None,
        **attrs: t.Any,
    ) -> None:
        if required is None:
            if attrs.get("default") is not None:
                required = False
            else:
                required = attrs.get("nargs", 1) > 0

        if "multiple" in attrs:
            raise TypeError("__init__() got an unexpected keyword argument 'multiple'.")

        super().__init__(param_decls, required=required, **attrs)

        if __debug__:
            if self.default is not None and self.nargs == -1:
                raise TypeError("'default' is not supported for nargs=-1.")

    @property
    def human_readable_name(self) -> str:
        if self.metavar is not None:
            return self.metavar
        return self.name.upper()  # type: ignore

    def make_metavar(self) -> str:
        if self.metavar is not None:
            return self.metavar
        var = self.type.get_metavar(self)
        if not var:
            var = self.name.upper()  # type: ignore
        if not self.required:
            var = f"[{var}]"
        if self.nargs != 1:
            var += "..."
        return var

    def _parse_decls(
        self, decls: cabc.Sequence[str], expose_value: bool
    ) -> tuple[str | None, list[str], list[str]]:
        if not decls:
            if not expose_value:
                return None, [], []
            raise TypeError("Could not determine name for argument")
        if len(decls) == 1:
            name = arg = decls[0]
            name = name.replace("-", "_").lower()
        else:
            raise TypeError(
                "Arguments take exactly one parameter declaration, got"
                f" {len(decls)}."
            )
        return name, [arg], []

    def get_usage_pieces(self, ctx: Context) -> list[str]:
        return [self.make_metavar()]

    def get_error_hint(self, ctx: Context) -> str:
        return f"'{self.make_metavar()}'"

    def add_to_parser(self, parser: _OptionParser, ctx: Context) -> None:
        parser.add_argument(dest=self.name, nargs=self.nargs, obj=self)
