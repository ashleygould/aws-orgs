#!/usr/bin/env python
'''
Install initial aws-orgs config and spec files into user's home directory

Usage:
  awsorgs-spec-init [-h | --help] [--config File] [--spec-dir PATH]

Options:
  -h, --help        Show this message and exit.
  --config FILE     Where to install AWS Org config file
                    [Default: ~/.awsorgs/config.yaml].
  --spec-dir PATH   Where to install AWS Org specification files
                    [Default: ~/.awsorgs/spec.d].
'''


import os
import sys
import shutil
import pkg_resources
from docopt import docopt


def main():
    args = docopt(__doc__)
    errors = []
    source_dir = os.path.abspath(
        pkg_resources.resource_filename(__name__, '../spec_init_data')
    )
    source_config_file = os.path.join(source_dir, 'config.yaml')
    source_spec_dir = os.path.join(source_dir, 'spec.d')
    target_config_file = os.path.expanduser(args['--config'])
    target_config_dir = os.path.dirname(target_config_file)
    target_spec_dir = os.path.expanduser(args['--spec-dir'])

    if os.path.exists(target_config_file):
        errors.append(
            "Config file '{}' exists. "
            "Refusing to overwrite.".format(target_config_file)
        )
    else:
        try:
            os.makedirs(target_config_dir)
        except OSError:
            if not os.path.isdir(target_config_dir):
                raise
        shutil.copy(source_config_file, target_config_file)

    try:
        os.makedirs(target_spec_dir)
    except OSError:
        if not os.path.isdir(target_spec_dir):
            raise
    if os.listdir(target_spec_dir):
        errors.append(
            "Spec directory '{}' exists and is not empty. "
            "Refusing to overwrite.".format(target_spec_dir)
        )
    else:
        for file in os.listdir(source_spec_dir):
            shutil.copy(
                os.path.join(source_spec_dir, file),
                target_spec_dir,
            )

    if errors:
        sys.exit('\n'.join(errors))


if __name__ == "__main__":
    main()
