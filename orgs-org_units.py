#!/usr/bin/python
#
# Manage Organizaion OUs

# TODO:
# DONE add --dryrun flag
# manage accounts
# DONE detach non-specified policies
# validate policy spec is an array of str

import boto3
import yaml
import sys
import os
import argparse # required 'pip install argparse'


#
# process command line args
#
parser = argparse.ArgumentParser(description='Manage AWS Organization')
parser.add_argument('--dryrun',
    help='dryrun mode. show pending changes, but do nothing',
    action='store_true'
)
parser.add_argument('--silent',
    help='silent mode. overriden when --dryrun is set',
    action='store_true'
)
parser.add_argument('spec_file',
    type=file,
    help='file containing organization specification in yaml format'
)
args = parser.parse_args()


#
# Variables
#

# can't be silent when it's a dryrun
if args.dryrun: args.silent = False

# get org_spec from yaml file
org_spec = yaml.load(args.spec_file.read())

# determine the Organization Root ID
org_client = boto3.client('organizations')
root_id = org_client.list_roots()['Roots'][0]['Id']

# enable policy type in the Organization root
policy_types = org_client.describe_organization()['Organization']['AvailablePolicyTypes']
for p_type in policy_types:
    if p_type['Type'] == 'SERVICE_CONTROL_POLICY' and p_type['Status'] != 'ENABLED':
        response = org_client.enable_policy_type(
            RootId=root_id,
            PolicyType='SERVICE_CONTROL_POLICY'
        )

# all orgs have at least this default policy
default_policy = 'FullAWSAccess'





#
# Specification parsing functions
#
def is_child(spec_ou):
    if 'OU' in spec_ou and spec_ou['OU'] != None and len(spec_ou['OU']) != 0:
        return True
    else:
        return False

def ensure_absent(spec_ou):
    if 'Ensure' in spec_ou and spec_ou['Ensure'] == 'absent':
        return True
    else:
        return False

def get_policy_spec(spec_ou):
    if 'Policy' in spec_ou and spec_ou['Policy'] != None:
        return [default_policy] + spec_ou['Policy']
    else:
        return [default_policy]




#
# OrganizaionalUnit functions
#

# create an ou under specified parent
def create_ou (parent_id, ou_name):
    new_ou = org_client.create_organizational_unit(
        ParentId=parent_id,
        Name=ou_name
    )
    return new_ou

# delete ou
def delete_ou (ou_id):
    org_client.delete_organizational_unit(
        OrganizationalUnitId=ou_id,
    )

# return ou name from an ou id
def get_ou_name(ou_id):
    if ou_id == root_id:
        return 'Root'
    else:
        ou = org_client.describe_organizational_unit(
            OrganizationalUnitId=ou_id
        )['OrganizationalUnit']
        return ou['Name']




#
# Policy functions
#

# get policy id based on it's name
def get_policy_id_by_name(policy_name):
    response = org_client.list_policies(Filter='SERVICE_CONTROL_POLICY')['Policies']
    for policy in response:
        if policy['Name'] == policy_name:
            return policy['Id']

# returns a list of service control policies attached to an OU
def get_policies (ou_id):
    response = org_client.list_policies_for_target(
        TargetId=ou_id,
        Filter='SERVICE_CONTROL_POLICY',
    )['Policies']
    return response

# returns sorted list of service control policy names from a list of policies
def get_policy_names (policy_list):
    names = []
    for policy in policy_list:
        names.append(policy['Name'])
    return sorted(names)

# verify if a policy is attached to an ou
def policy_attached(policy_id, ou_id,):
    for policy in get_policies(ou_id): 
        if policy['Id'] == policy_id:
            return True
    return False

# attach a policy to an ou
def attach_policy (policy_id, ou_id,):
    response = org_client.attach_policy (
        PolicyId=policy_id,
        TargetId=ou_id
    )

# detach a policy to an ou
def detach_policy (policy_id, ou_id,):
    org_client.detach_policy (
        PolicyId=policy_id,
        TargetId=ou_id
    )



#
# display_existing_organization()
#
# recursive function to display the existing org structure 
def display_existing_organization (parent_name, parent_id, indent):
    # query aws for child orgs
    child_ou_list = org_client.list_children(
        ParentId=parent_id,
        ChildType='ORGANIZATIONAL_UNIT'
    )['Children']
    # print parent ou name
    tab = '  '
    #print
    print tab*indent + parent_name + ':'
    # look for policies
    policy_list = get_policies(parent_id)
    if len(policy_list) > 0:
        print tab*indent + tab + 'policies: ' + ' '.join(get_policy_names(policy_list))
    # look for child OUs
    if len(child_ou_list ) > 0:
        print tab*indent + tab + 'child_ou:'
        indent+=2
        for ou in child_ou_list:
            # recurse
            display_existing_organization(get_ou_name(ou['Id']), ou['Id'], indent)



#
# manage_ou()
#
# Recursive function to reconcile state of deployed OUs with OU specification
def manage_ou (specified_ou_list, parent_id):
    # query for child OU
    existing_ou_list = org_client.list_organizational_units_for_parent(
        ParentId=parent_id,
    )['OrganizationalUnits']

    # create hash of existing ou names:IDs
    existing_ou_names = {}
    for ou in existing_ou_list:
        existing_ou_names[ou['Name']] = ou['Id']

    # check if each specified OU exists
    for spec_ou in specified_ou_list:
        if spec_ou['Name'] in existing_ou_names.keys():
            existing_ou_id = existing_ou_names[spec_ou['Name']]
            # test for child ou in ou_spec
            if is_child(spec_ou):
                # recurse
                manage_ou(spec_ou['OU'], existing_ou_id)

            # test if ou should be 'absent'
            if ensure_absent(spec_ou):
                if not args.silent:
                    print 'deleting OU', spec_ou['Name']
                if not args.dryrun:
                    delete_ou(existing_ou_id)

            # attach specified policies
            policy_spec = get_policy_spec(spec_ou)
            for policy_name in policy_spec:
                policy_id = get_policy_id_by_name(policy_name)
                if not policy_attached(policy_id, existing_ou_id) and not ensure_absent(spec_ou):
                    if not args.silent:
                        print "attaching policy %s to OU %s" % (policy_name, spec_ou['Name'])
                    if not args.dryrun:
                        attach_policy(policy_id, existing_ou_id)

            # detach unspecified policies
            existing_policy_list = get_policy_names(get_policies(existing_ou_id))
            for policy_name in existing_policy_list:
                if policy_name not in policy_spec and not ensure_absent(spec_ou):
                    policy_id = get_policy_id_by_name(policy_name)
                    if not args.silent:
                        print "detaching policy %s from OU %s" % (policy_name, spec_ou['Name'])
                    if not args.dryrun:
                        detach_policy(policy_id, existing_ou_id)

        # ou does not exist
        elif not ensure_absent(spec_ou):
            # create new ou
            if not args.silent:
                print "creating new ou %s under parent %s" % (spec_ou['Name'], get_ou_name(parent_id))
            if not args.dryrun: 
                new_ou = create_ou(parent_id, spec_ou['Name'])
                # test if new ou should have children
                if is_child(spec_ou) and isinstance(new_ou, dict) \
                        and 'Id' in new_ou['OrganizationalUnit']:
                    # recurse
                    manage_ou(spec_ou['OU'], new_ou['OrganizationalUnit']['Id'])



#
# Main
#
if args.silent:
    manage_ou (org_spec['Org']['OU'], root_id)
else:
    print 'Existing org:'
    display_existing_organization('root', root_id, 0)
    print
    if args.dryrun:
        print "This is a dry run!"
        print "Pending Actions:"
    else:
        print "Actions taken:"
    manage_ou (org_spec['Org']['OU'], root_id)
    if not args.dryrun:
        print
        print 'Resulting org:'
        display_existing_organization('root', root_id, 0)




