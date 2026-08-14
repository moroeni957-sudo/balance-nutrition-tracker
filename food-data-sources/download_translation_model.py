"""Download and install the Argos English-to-Russian model into a local directory."""

from argostranslate import package


def main():
    package.update_package_index()
    available = package.get_available_packages()
    model = next(item for item in available if item.from_code == "en" and item.to_code == "ru")
    archive = model.download()
    package.install_from_path(archive)
    print(f"Installed English-to-Russian model {model.package_version}")


if __name__ == "__main__":
    main()
