import functools
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from packaging.version import Version

from lightning_app import _logger, _PROJECT_ROOT, _root_logger
from lightning_app.__version__ import version
from lightning_app.core.constants import PREPARE_LIGHTING
from lightning_app.utilities.git import check_github_repository, get_dir_name

logger = logging.getLogger(__name__)


# FIXME(alecmerdler): Use GitHub release artifacts once the `lightning-ui` repo is public
LIGHTNING_FRONTEND_RELEASE_URL = "https://storage.googleapis.com/grid-packages/lightning-ui/v0.0.0/build.tar.gz"


def download_frontend(root):
    """Downloads an archive file for a specific release of the Lightning frontend and extracts it to the correct
    directory."""
    build_dir = "build"
    frontend_dir = pathlib.Path(root, "src", "lightning_app", "ui")
    download_dir = tempfile.mkdtemp()

    shutil.rmtree(frontend_dir, ignore_errors=True)

    response = urllib.request.urlopen(LIGHTNING_FRONTEND_RELEASE_URL)

    file = tarfile.open(fileobj=response, mode="r|gz")
    file.extractall(path=download_dir)

    shutil.move(os.path.join(download_dir, build_dir), frontend_dir)
    print("The Lightning UI has successfully been downloaded!")


def _cleanup(*tar_files: str):
    for tar_file in tar_files:
        shutil.rmtree(os.path.join(_PROJECT_ROOT, "dist"), ignore_errors=True)
        os.remove(tar_file)


def _prepare_wheel(path):
    with open("log.txt", "w") as logfile:
        with subprocess.Popen(
            ["rm", "-r", "dist"], stdout=logfile, stderr=logfile, bufsize=0, close_fds=True, cwd=path
        ) as proc:
            proc.wait()
        with subprocess.Popen(
            ["python", "setup.py", "sdist"],
            stdout=logfile,
            stderr=logfile,
            bufsize=0,
            close_fds=True,
            cwd=path,
        ) as proc:
            proc.wait()

    os.remove("log.txt")


def _copy_tar(project_root, dest: Path) -> str:
    dist_dir = os.path.join(project_root, "dist")
    tar_files = os.listdir(dist_dir)
    assert len(tar_files) == 1
    tar_name = tar_files[0]
    tar_path = os.path.join(dist_dir, tar_name)
    shutil.copy(tar_path, dest)
    return tar_name


def get_dist_path_if_editable_install(project_name) -> str:
    """Is distribution an editable install - modified version from pip that
    fetches egg-info instead of egg-link"""
    for path_item in sys.path:
        egg_info = os.path.join(path_item, project_name + ".egg-info")
        if os.path.isdir(egg_info):
            return path_item
    return ""


def _prepare_lightning_wheels_and_requirements(root: Path) -> Optional[Callable]:

    if "site-packages" in _PROJECT_ROOT:
        return

    # Packaging the Lightning codebase happens only inside the `lightning` repo.
    git_dir_name = get_dir_name() if check_github_repository() else None

    if not PREPARE_LIGHTING and (not git_dir_name or (git_dir_name and not git_dir_name.startswith("lightning"))):
        return
    if not bool(int(os.getenv("SKIP_LIGHTING_WHEELS_BUILD", "0"))):
        download_frontend(_PROJECT_ROOT)
        _prepare_wheel(_PROJECT_ROOT)

    logger.info("Packaged Lightning with your application.")

    tar_name = _copy_tar(_PROJECT_ROOT, root)

    tar_files = [os.path.join(root, tar_name)]

    # skipping this by default
    if not bool(int(os.getenv("SKIP_LIGHTING_UTILITY_WHEELS_BUILD", "1"))):
        # building and copying launcher wheel if installed in editable mode
        launcher_project_path = get_dist_path_if_editable_install("lightning_launcher")
        if launcher_project_path:
            _prepare_wheel(launcher_project_path)
            tar_name = _copy_tar(launcher_project_path, root)
            tar_files.append(os.path.join(root, tar_name))

        # building and copying lightning-cloud wheel if installed in editable mode
        lightning_cloud_project_path = get_dist_path_if_editable_install("lightning_cloud")
        if lightning_cloud_project_path:
            _prepare_wheel(lightning_cloud_project_path)
            tar_name = _copy_tar(lightning_cloud_project_path, root)
            tar_files.append(os.path.join(root, tar_name))

    return functools.partial(_cleanup, *tar_files)


def _enable_debugging():
    tar_file = os.path.join(os.getcwd(), f"lightning-{version}.tar.gz")

    if not os.path.exists(tar_file):
        return

    _root_logger.propagate = True
    _logger.propagate = True
    _root_logger.setLevel(logging.DEBUG)
    _root_logger.debug("Setting debugging mode.")


def enable_debugging(func: Callable) -> Callable:
    """This function is used to transform any print into logger.info calls, so it gets tracked in the cloud."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        _enable_debugging()
        res = func(*args, **kwargs)
        _logger.setLevel(logging.INFO)
        return res

    return wrapper


def _fetch_latest_version(package_name: str) -> str:
    args = [
        sys.executable,
        "-m",
        "pip",
        "install",
        f"{package_name}==1000",
    ]

    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, close_fds=True)
    if proc.stdout:
        logs = " ".join([line.decode("utf-8") for line in iter(proc.stdout.readline, b"")])
        return logs.split(")\n")[0].split(",")[-1].replace(" ", "")
    return version


def _verify_lightning_version():
    """This function verifies that users are running the latest lightning version for the cloud."""
    # TODO (tchaton) Add support for windows
    if sys.platform == "win32":
        return

    lightning_latest_version = _fetch_latest_version("lightning")

    if Version(lightning_latest_version) > Version(version):
        raise Exception(
            f"You need to use the latest version of Lightning ({lightning_latest_version}) to run in the cloud. "
            "Please, run `pip install -U lightning`"
        )
