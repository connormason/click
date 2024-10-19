from __future__ import annotations

import collections.abc as cabc
import typing as t
from contextlib import contextmanager
from itertools import repeat

from click.exceptions import BadParameter
from click.exceptions import UsageError

if t.TYPE_CHECKING:
    from click.core.commands import Command
    from click.core.commands import Group
    from click.core.context import Context
    from click.core.params import Parameter


V = t.TypeVar("V")


def _complete_visible_commands(
    ctx: Context, incomplete: str
) -> cabc.Iterator[tuple[str, Command]]:
    """List all the subcommands of a group that start with the
    incomplete value and aren't hidden.

    :param ctx: Invocation context for the group.
    :param incomplete: Value being completed. May be empty.
    """
    from click.core.commands import Group

    multi = t.cast(Group, ctx.command)

    for name in multi.list_commands(ctx):
        if name.startswith(incomplete):
            command = multi.get_command(ctx, name)

            if command is not None and not command.hidden:
                yield name, command


def _check_nested_chain(
    base_command: Group, cmd_name: str, cmd: Command, register: bool = False
) -> None:
    from click.core.commands import Group

    if not base_command.chain or not isinstance(cmd, Group):
        return

    if register:
        message = (
            f"It is not possible to add the group {cmd_name!r} to another"
            f" group {base_command.name!r} that is in chain mode."
        )
    else:
        message = (
            f"Found the group {cmd_name!r} as subcommand to another group "
            f" {base_command.name!r} that is in chain mode. This is not supported."
        )

    raise RuntimeError(message)


def batch(iterable: cabc.Iterable[V], batch_size: int) -> list[tuple[V, ...]]:
    return list(zip(*repeat(iter(iterable), batch_size)))


@contextmanager
def augment_usage_errors(
    ctx: Context, param: Parameter | None = None
) -> cabc.Iterator[None]:
    """Context manager that attaches extra information to exceptions."""
    try:
        yield
    except BadParameter as e:
        if e.ctx is None:
            e.ctx = ctx
        if param is not None and e.param is None:
            e.param = param
        raise
    except UsageError as e:
        if e.ctx is None:
            e.ctx = ctx
        raise


def iter_params_for_processing(
    invocation_order: cabc.Sequence[Parameter],
    declaration_order: cabc.Sequence[Parameter],
) -> list[Parameter]:
    """Given a sequence of parameters in the order as should be considered
    for processing and an iterable of parameters that exist, this returns
    a list in the correct order as they should be processed.
    """

    def sort_key(item: Parameter) -> tuple[bool, float]:
        try:
            idx: float = invocation_order.index(item)
        except ValueError:
            idx = float("inf")

        return not item.is_eager, idx

    return sorted(declaration_order, key=sort_key)


def _check_iter(value: t.Any) -> cabc.Iterator[t.Any]:
    """Check if the value is iterable but not a string. Raises a type
    error, or return an iterator over the value.
    """
    if isinstance(value, str):
        raise TypeError

    return iter(value)
