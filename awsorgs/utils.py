"""Utility functions used by the various awsorgs modules"""

import os
import pkg_resources
import re

import boto3
import yaml
import logging

def lookup(dlist, lkey, lvalue, rkey=None):
    """
    Use a known key:value pair to lookup a dictionary in a list of
    dictionaries.  Return the dictonary or None.  If rkey is provided,
    return the value referenced by rkey or None.  If more than one
    dict matches, raise an error.
    args:
        dlist:   lookup table -  a list of dictionaries
        lkey:    name of key to use as lookup criteria
        lvalue:  value to use as lookup criteria
        key:     (optional) name of key referencing a value to return
    """
    items = [d for d in dlist
             if lkey in d
             and d[lkey] == lvalue]
    if not items:
        return None
    if len(items) > 1:
        raise RuntimeError(
            "Data Error: lkey:lvalue lookup matches multiple items in dlist"
        )
    if rkey:
        if rkey in items[0]:
            return items[0][rkey]
        return None
    return items[0]


def ensure_absent(spec):
    """
    test if an 'Ensure' key is set to absent in dictionary 'spec'
    """
    if 'Ensure' in spec and spec['Ensure'] == 'absent': return True
    return False


def get_logger(args):
    """
    Setup logging.basicConfig from args.
    Return logging.Logger object.
    """
    # log level
    log_level = logging.CRITICAL
    if args['--verbose'] or args['report'] or args['--boto-log']:
        log_level = logging.INFO
    if args['--debug']:
        log_level = logging.DEBUG
    # log format
    log_format = '%(name)s: %(levelname)-8s%(message)s'
    if args['report']:
        log_format = '%(message)s'
    if args['--debug']:
        log_format = '%(name)s: %(levelname)-8s%(funcName)s():  %(message)s'
    if not args['--exec']:
        log_format = '[dryrun] %s' % log_format
    if not args['--boto-log']:
        logging.getLogger('botocore').propagate = False
        logging.getLogger('boto3').propagate = False
    logging.basicConfig(format=log_format, level=log_level)
    log = logging.getLogger(__name__)
    return log


def get_root_id(org_client):
    """
    Query deployed AWS Organization for its Root ID.
    """
    roots = org_client.list_roots()['Roots']
    if len(roots) >1:
        raise RuntimeError("org_client.list_roots returned multiple roots.")
    return roots[0]['Id']


def validate_master_id(org_client, spec):
    """
    Don't mangle the wrong org by accident
    """
    master_account_id = org_client.describe_organization(
      )['Organization']['MasterAccountId']
    if master_account_id != spec['master_account_id']:
        errmsg = ("""The Organization Master Account Id '%s' does not
          match the 'master_account_id' set in the spec-file.  
          Is your '--profile' arg correct?""" % master_account_id)
        raise RuntimeError(errmsg)
    return


# QUESTION: I'm loading a data file by name.  It is part of the project and
# explicitly installed by setup.py.  but in code, should I define it as
# a constant insted of just loading a str?
def load_validation_patterns():
    """
    Return dict of patterns for use when validating specification syntax
    """
    filename =  os.path.abspath(pkg_resources.resource_filename(
            __name__, '../data/spec-validation-patterns.yaml'))
    with open(filename) as f:
        return yaml.load(f.read())


def validate_spec(log, spec_patterns, pattern_name, spec):
    """
    Validate syntax of a given 'spec' dictionary against the
    named spec_pattern.
    """
    pattern = spec_patterns[pattern_name]
    valid_spec = True
    caller = 'validate_spec'
    log.debug("validating spec against pattern '%s'" % (pattern_name))
    # test for required attributes
    required_attributes = [attr for attr in pattern if pattern[attr]['required']]
    log.debug("required attributes for pattern '%s' : %s" %
            (pattern_name, required_attributes))
    for attr in required_attributes:
        if attr not in spec:
            log.error("Required attribute '%s' not found in '%s' spec.  Must "
                    "be one of %s" % (attr, pattern_name, required_attributes))
            valid_spec = False
    for attr in spec:
        log.debug("considering attribute '%s'" % (attr))
        # test if attribute is permitted
        if attr not in pattern:
            log.warn("Attribute '%s' not present spec valdation pattern '%s'" %
                    (attr, pattern_name))
            continue
        # test attribute type. ignore attr if value is None
        if spec[attr]:
            # (surely there must be a better way to extract the data type of
            # an object as a string)
            spec_attr_type = re.sub(r"<type '(\w+)'>", '\g<1>', str(type(spec[attr])))
            log.debug("spec attribute type: '%s'" % (spec_attr_type))
            # simple attribute pattern
            if isinstance(pattern[attr]['atype'], str):
                log.debug("pattern attribute type: '%s'" %
                        (pattern[attr]['atype']))
                if spec_attr_type != pattern[attr]['atype']:
                    log.error("Attribute '%s' must be of type '%s'" %
                            (attr, pattern[attr]['atype']))
                    valid_spec = False
                    continue
            else:
                # complex attribute pattern
                valid_types = pattern[attr]['atype'].keys()
                log.debug("pattern attribute types: '%s'" % (valid_types))
                if not spec_attr_type in valid_types: 
                    log.error("Attribute '%s' must be one of type '%s'" %
                            (attr, valid_types))
                    valid_spec = False
                    continue
                atype = pattern[attr]['atype'][spec_attr_type]
                # test attributes values
                if atype and 'values' in atype:
                    log.debug("allowed values for attrubute '%s': %s" %
                            (attr, atype['values']))
                    if not spec[attr] in atype['values']:
                        log.error("Value of attribute '%s' must be one of '%s'" %
                                (attr, atype['values']))
                        valid_spec = False
                        continue
    return valid_spec
