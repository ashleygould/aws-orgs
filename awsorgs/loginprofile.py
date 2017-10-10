#!/usr/bin/env python
"""Manage AWS IAM user login profile.

Usage:
  awsloginprofile USER [-vd] [--boto-log]
  awsloginprofile USER --disable [-vd] [--boto-log]
  awsloginprofile USER --disable-expired [--opt-ttl HOURS] [-vd] [--boto-log]
  awsloginprofile USER (--new | --reset | --reenable) [-vd] [--boto-log]
                       [--password PASSWORD] [--email EMAIL]
  awsloginprofile --help

Options:
  USER                     Name of IAM user.
  --new                    Create new login profile.
  --reset                  Reset password for existing login profile.
  --disable                Delete existing login profile, disable access keys.
  --disable-expired        Delete profile if one-time-password exceeds --opt-ttl.
  --reenable               Recreate login profile, reactivate access keys.
  --opt-ttl HOURS          One-time-password time to live in hours [default: 24].
  --password PASSWORD      Supply password, do not require user to reset.
  --email EMAIL            Supply user's email address for sending credentials.
                           (not implemented yet)
  -h, --help               Show this help message and exit.
  -V, --version            Display version info and exit.
  -v, --verbose            Log to activity to STDOUT at log level INFO.
  -d, --debug              Increase log level to 'DEBUG'. Implies '--verbose'.
  --boto-log               Include botocore and boto3 logs in log stream.

"""


import os
import sys
import yaml
import logging
from string import Template
import datetime

import boto3
from botocore.exceptions import ClientError
from docopt import docopt
from passgen import passgen

from awsorgs.utils import *


# Relative path within awsorgs project to template file used by prep_email()
EMAIL_TEMPLATE = 'data/email_template'


def get_user_name():
    """
    Returns the IAM user_name of the calling identidy (i.e. you)
    """
    sts = boto3.client('sts')
    return sts.get_caller_identity()['Arn'].split('/')[-1]


def list_delegations(user):
    """Return list of assume_role resource arns for all groups for user"""
    groups = list(user.groups.all())
    assume_role_policies = []
    for group in user.groups.all():
        assume_role_policies += [p for p in list(group.policies.all())
                if p.policy_document['Statement'][0]['Action'] == 'sts:AssumeRole']
    return [policy.policy_document['Statement'][0]['Resource'] for policy
            in assume_role_policies]


def format_delegation_table(delegation_arns):
    """Generate formatted list of delegation attributes as printable string"""
    tpl = """
  account_id:   $account_id
  role_name:    $role_name
  role_arn:     $role_arn
"""
    delegation_string = ''
    for assume_role_arn in delegation_arns:
        delegation_string += Template(tpl).substitute(dict(
                role_arn=assume_role_arn,
                role_name=assume_role_arn.partition('role/')[2],
                account_id=assume_role_arn.split(':')[4]))
    return delegation_string


def prep_email(log, user, passwd, email):
    """Generate email body from template"""
    log.debug("loading file: '%s'" % EMAIL_TEMPLATE)
    delegation_table = list_delegations(user)
    log.debug('delegation_table: %s' % delegation_table)
    template = os.path.abspath(pkg_resources.resource_filename(__name__, EMAIL_TEMPLATE))
    mapping = dict(
            user_name=user.name,
            onetimepw=passwd,
            trusted_id=boto3.client('sts').get_caller_identity()['Account'],
            delegations=format_delegation_table(delegation_table),
    )
    with open(template) as tpl:
        print(Template(tpl.read()).substitute(mapping))


def validate_user(user_name):
    """Return a valid IAM User object"""
    iam = boto3.resource('iam')
    user = iam.User(user_name)
    try:
        user.load()
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            return    
    return user


def validate_login_profile(user):
    """Return a valid IAM LoginProfile object"""
    login_profile = user.LoginProfile()
    try:
        login_profile.load()
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            return
    return login_profile


def munge_passwd(passwd=None):
    """Return new 'passwd' string and boolean 'require_reset'.
    If passwd provided, set 'require_reset' to False.
    """
    if passwd:
        require_reset = False
    else:
        passwd = passgen()
        require_reset = True
    return passwd, require_reset


def create_profile(log, user, passwd, require_reset):
    log.info('creating login profile for user %s' % user.name)
    return user.create_login_profile(
            Password=passwd,
            PasswordResetRequired=require_reset)


def reset_profile(log, user, login_profile, passwd, require_reset):
    """Reset IAM user passwd by deleting and recreating login profile.
    This ensures the password creation date gets reset when updating a password.
    """
    if login_profile:
        log.info('resetting login profile for user %s' % user.name)
        login_profile.delete()
        return login_profile.create(
                Password=passwd,
                PasswordResetRequired=require_reset)
    else:
        log.error("user '%s' has no login profile" % user.name)
        sys.exit(1)

def delete_profile(log, user, login_profile):
    if login_profile:
        log.info('deleting login profile for user %s' % user.name)
        login_profile.delete()
    else:
        log.warn("user '%s' has no login profile" % user.name)


def set_access_key_status(log, user, enable=True):
    """Enable or disable an IAM user's access keys"""
    for key in user.access_keys.all():
        if enable and key.status == 'Inactive':
            log.info('enabling access key %s for user %s' %
                    (key.access_key_id, user.name))
            key.activate()
        elif not enable and key.status == 'Active':
            log.info('disabling access key %s for user %s' %
                    (key.access_key_id, user.name))
            key.deactivate()


def onetime_passwd_expired(log, user, login_profile, hours):
    """Test if initial one-time-only password is expired"""
    if login_profile and login_profile.password_reset_required:
        now = datetime.datetime.now(datetime.timezone.utc)
        log.debug('now: %s' % now.isoformat())
        log.debug('ttl: %s' % datetime.timedelta(hours=hours))
        log.debug('delta: %s' % (now - login_profile.create_date))
        return (now - login_profile.create_date) > datetime.timedelta(hours=hours)
    return False


def user_report(log, user, login_profile):
    """Generate report of IAM user's login profile, password usage, and
    assume_role delegations for any groups user is member of.
    """
    log.info('User:                    %s' % user.name)
    log.info('User Id:                 %s' % user.user_id)
    log.info('User created:            %s' % user.create_date)
    if login_profile:
        log.info('Login profile created:   %s' % login_profile.create_date)
        log.info('Passwd reset required:   %s' % login_profile.password_reset_required)
        if login_profile.password_reset_required:
            log.info('One-time-passwd age:     %s' %
                    (datetime.datetime.now(datetime.timezone.utc)
                    - login_profile.create_date))
        else:
            log.info('Password last used:      %s' % user.password_last_used)
    else:
        log.info('User login profile:      %s' % login_profile)
    assume_role_arns = list_delegations(user)
    if assume_role_arns:
        log.info('Delegations:\n  %s' % '\n  '.join(assume_role_arns))


def main():
    args = docopt(__doc__, version='0.0.6.rc1')
    # HACK ALERT!
    # set '--exec' and 'report' args to make get_logger() happy
    args['--exec'] = True
    if not (args['--new']
            or args['--reset']
            or args['--disable']
            or args['--disable-expired']
            or args['--reenable']):
        args['report'] = True
    else:
        args['report'] = False
    log = get_logger(args)
    log.debug(args)

    user = validate_user(args['USER'])
    if not user:
        log.critical('no such user: %s' % args['USER'])
        sys.exit(1)

    login_profile = validate_login_profile(user)
    passwd, require_reset = munge_passwd(args['--password'])

    if args['--new']:
        if not login_profile:
            login_profile = create_profile(log, user, passwd, require_reset)
            prep_email(log, user, passwd, args['--email'])
        else:
            log.warn("login profile for user '%s' already exists" % user.name)
        user_report(log, user, login_profile)

    elif args['--reset']:
        login_profile = reset_profile(log, user, login_profile, passwd, require_reset)
        prep_email(log, user, passwd, args['--email'])
        user_report(log, user, login_profile)

    elif args['--disable']:
        delete_profile(log, user, login_profile)
        set_access_key_status(log, user, False)

    elif args['--disable-expired']:
        if onetime_passwd_expired(log, user, login_profile, int(args['--opt-ttl'])):
            delete_profile(log, user, login_profile)

    elif args['--reenable']:
        if not login_profile:
            login_profile = create_profile(log, user, passwd, require_reset)
            prep_email(log, user, passwd, args['--email'])
        else:
            log.warn("login profile for user '%s' already exists" % user.name)
        set_access_key_status(log, user, True)
        user_report(log, user, login_profile)

    else:
        user_report(log, user, login_profile)


if __name__ == "__main__":
    main()
