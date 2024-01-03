import setuptools
from outdated import check_outdated
from monarchmoneyamazontagger import VERSION
from distutils.errors import DistutilsError


with open("README.md", "r") as fh:
    long_description = fh.read()


class CleanCommand(setuptools.Command):
    """Custom clean command to tidy up the project root."""
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        import shutil
        dirs = [
            'build',
            'dist',
            'tagger-release',
            'target',
            'release_venv',
            'cache',
            'monarchmoney_amazon_tagger.egg-info',
        ]
        for tree in dirs:
            shutil.rmtree(tree, ignore_errors=True)
        import os
        from glob import glob
        globs = ('**/*.pyc', '**/*.tgz', '**/*.pyo')
        for g in globs:
            for file in glob(g, recursive=True):
                try:
                    os.remove(file)
                except OSError:
                    print(f"Error while deleting file: {file}")


class BlockReleaseCommand(setuptools.Command):
    """Raises an error if VERSION is already present on PyPI."""
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        try:
            stale, latest = check_outdated('monarchmoney-amazon-tagger', VERSION)
            raise DistutilsError(
                'Please update VERSION in __init__. '
                f'Current {VERSION} PyPI latest {latest}')
        except ValueError:
            pass


setuptools.setup(
    name="monarchmoney-amazon-tagger",
    version=VERSION,
    author="Jeff Prouty",
    author_email="jeff.prouty@gmail.com",
    description=("Fetches your Amazon order history and matching/tags your "
                 "Monarch Money transactions"),
    keywords='amazon monarch money tagger transactions order history',
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/jprouty/monarchmoney-amazon-tagger",
    packages=setuptools.find_packages(),
    python_requires='>=3',
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Office/Business :: Financial",
    ],
    install_requires=[
        'PyQt6',
        'mock',
        'outdated',
        'progress',
        'range-key-dict',
    ],
    entry_points=dict(
        console_scripts=[
            'monarchmoney-amazon-tagger-cli=monarchmoneyamazontagger.cli:main',
            'monarchmoney-amazon-tagger=monarchmoneyamazontagger.main:main',
            'monarchmoney-amazon-tagger-repro_selenium_issue=monarchmoneyamazontagger.repro_mac_issue:main'
        ],
    ),
    cmdclass={
        'clean': CleanCommand,
        'block_on_version': BlockReleaseCommand,
    },
)
