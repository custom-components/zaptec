#!/usr/bin/env python
"""Script for checking requirements wrt HA."""

import argparse
import json
from pathlib import Path
import sys
import urllib.request

from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "zaptec" / "manifest.json"
HACS = ROOT / "hacs.json"
CONSTRAINTS_URL = "https://raw.githubusercontent.com/home-assistant/core/{v}/homeassistant/package_constraints.txt"
HA_RELEASES_URL = "https://api.github.com/repos/home-assistant/core/tags"
PYPI_URL = "https://pypi.org/pypi/{package}/json"


def load_manifest_requirements() -> list[Requirement]:
    with MANIFEST.open() as f:
        manifest = json.load(f)
        reqs: list[str] = manifest.get("requirements", [])
        return [Requirement(req) for req in reqs]


def load_minimum_ha_version() -> Version:
    with HACS.open() as f:
        hacs = json.load(f)
        return Version(hacs.get("homeassistant", None))


def load_relevant_constraints(url: str, reqs: list[Requirement]) -> dict[str, Requirement]:
    names = [req.name for req in reqs]
    constraints: dict[str, Requirement] = {}
    with urllib.request.urlopen(url) as response:  # noqa: S310 (ignore suspicious-url-open-usage since url is hardcoded https)
        for raw_line in response:
            line = raw_line.decode().strip()
            if not line or line.startswith("#"):
                continue

            constraint = Requirement(line)
            if constraint.name in names:
                constraints[constraint.name] = constraint
    return constraints


def get_pypi_versions_for_package(package: str) -> list[Version]:
    url = PYPI_URL.format(package=package)

    versions: list[Version] = []
    with urllib.request.urlopen(url) as resp:  # noqa: S310
        data = json.load(resp)

        for v in data.get("releases", {}):
            try:
                versions.append(Version(v))
            except InvalidVersion:
                continue
    return versions


def get_pypi_versions(packages: list[str]) -> dict[str, list[Version]]:
    versions: dict[str, list[Version]] = {}
    for package in packages:
        versions[package] = get_pypi_versions_for_package(package)
    return versions


def check_compatibility(
    requirements: list[Requirement],
    constraints: dict[str, Requirement],
    pypi_lib: dict[str, list[Version]],
) -> list[str]:
    errors: list[str] = []
    for req in requirements:
        if req.name not in constraints:
            print(f"No constraints on {req.name}")
            continue

        constraint = constraints[req.name]
        versions = pypi_lib[req.name]
        valid_versions_ha = list(constraint.specifier.filter(versions))
        valid_versions = list(req.specifier.filter(valid_versions_ha))
        if valid_versions:
            print(f"{req.name} is satisfied by {valid_versions}")
        else:
            errors.append(
                f"{req.name} cannot be satisfied because {req.specifier} and "
                f"{constraint.specifier} are mutually exclusive"
            )

    return errors


def get_ha_tags() -> list[str]:
    tags = []
    page = 1
    last_page = 3
    while page <= last_page:
        url = f"{HA_RELEASES_URL}?per_page=100&page={page}"
        with urllib.request.urlopen(url) as resp:  # noqa: S310
            data = json.load(resp)
        if not data:
            break
        tags.extend(data)
        page += 1

    versions: list[str] = []
    for tag in tags:
        name: str = tag["name"]
        if name.startswith("202") and name.find("b") == -1:  # only 202x.y.z versions and not beta
            versions.append(name)

    return sorted(versions, reverse=True)


def get_constraints_url(version: str = "") -> str:
    return CONSTRAINTS_URL.format(v=version if version else "dev")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-ha-dev", action="store_true")
    parser.add_argument("--minimum-ha-version", action="store_true")
    args = parser.parse_args()

    if not any([args.current_ha_dev, args.minimum_ha_version]):
        print("Defaulting to both checks since none were specified")
        args.current_ha_dev = True
        args.minimum_ha_version = True

    requirements = load_manifest_requirements()

    if args.current_ha_dev:
        print("Checking manifest.json requirements against current dev branch of HA\n")
        constraints = load_relevant_constraints(get_constraints_url(), requirements)
        pypi_versions = get_pypi_versions(list(constraints.keys()))
        errors = check_compatibility(requirements, constraints, pypi_versions)

        if errors:
            print("Found incompatibilities with current HA dev:")
            for e in errors:
                print(" -", e)
            sys.exit(1)
        else:
            print("All requirements are compatible with Home Assistant constraints.\n")

    if args.minimum_ha_version:
        print("Checking manifest.json requirements against the minimum HA version in hacs.json\n")
        ha_versions = get_ha_tags()
        print(f"Checking HA tags: {ha_versions}")
        last_working = None
        minimum_ha_version = load_minimum_ha_version()
        pypi_versions: dict[str, list[Version]] = {}
        for ha_version in ha_versions:
            print(f"\nChecking HA version {ha_version}")
            constraints = load_relevant_constraints(get_constraints_url(ha_version), requirements)
            if not pypi_versions:
                pypi_versions = get_pypi_versions(list(constraints.keys()))
            errors = check_compatibility(requirements, constraints, pypi_versions)

            if errors:
                print(f"Found incompatibilities with HA version {ha_version}:")
                for e in errors:
                    print(" -", e)

                if not last_working:
                    print("Latest HA version is incompatible, please update manifest.json")
                    sys.exit(1)

                v = Version(last_working)
                if v > minimum_ha_version:
                    print(
                        f"Oldest compatible version was {last_working}, which "
                        f"is newer than {minimum_ha_version}, please update hacs.json."
                    )
                    sys.exit(1)
                elif v < minimum_ha_version:
                    print(
                        f"Oldest compatible version was {last_working}, minimum in hacs.json is "
                        f"{minimum_ha_version}, consider relaxing ha requirement."
                    )
                else:
                    print("Oldest compatible version matches hacs.json.")
                break
            else:
                last_working = ha_version
