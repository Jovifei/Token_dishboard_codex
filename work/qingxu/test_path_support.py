"""Link fixtures without changing Windows developer mode or elevation."""
import os


def directory_alias(link, target):
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def file_symlink(test, link, target):
    try:
        link.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            test.skipTest("Windows denies creation of a file symlink (WinError 1314)")
        raise
