from generators.learning.PlacePolicyMSECombinationOfQg import PlacePolicyMSECombinationOfQg
from generators.learning.PlacePolicyMSEFeedForward import PlacePolicyMSEFeedForward
from generators.learning.PickPolicyMSECombinationOfQg import PickPolicyMSECombinationOfQg
from generators.learning.PlacePolicyIMLECombinationOfQg import PlacePolicyIMLECombinationOfQg

from data_processing_utils import state_data_mode
from data_processing_utils import action_data_mode as default_action_data_mode

import socket

if socket.gethostname() == 'lab' or socket.gethostname() == 'phaedra' or socket.gethostname() == 'dell-XPS-15-9560':
    ROOTDIR = './'
else:
    ROOTDIR = '/data/public/rw/pass.port/guiding_gtamp_light/learned_weights/'


def load_weights(policy, seed, use_unregularized):
    if use_unregularized:
        weight_fname = 'without_regularizer/imle_pose_seed_%d.h5' % seed
    else:
        weight_fname = 'imle_pose_seed_%d.h5' % seed
    print "Loading weights at ", policy.save_folder + weight_fname
    policy.policy_model.load_weights(policy.save_folder + weight_fname)


def create_policy(config, n_collisions, given_action_data_mode=None):
    if given_action_data_mode is None:
        action_data_mode = default_action_data_mode
    else:
        action_data_mode = given_action_data_mode

    dim_collision = (n_collisions, 2, 1)

    if config.atype == 'pick':
        dim_action = 7
    else:
        dim_action = 4
    dim_pose = 24

    if ROOTDIR == './':
        if config.atype == 'place':
            savedir = './generators/learning/learned_weights/dtype_%s_state_data_mode_%s_action_data_mode_%s/%s/%s/' % \
                      (config.dtype, state_data_mode, action_data_mode, config.algo, config.region)
        else:
            savedir = './generators/learning/learned_weights/dtype_%s_state_data_mode_%s_action_data_mode_%s/%s/' % \
                      (config.dtype, state_data_mode, action_data_mode, config.algo)
    else:
        savedir = ''

    if config.algo == 'place_mse_qg_combination':
        policy = PlacePolicyMSEFeedForward(dim_action=dim_action, dim_collision=dim_collision, dim_pose=dim_pose,
                                           save_folder=savedir, config=config)
        #policy = PlacePolicyMSECombinationOfQg(dim_action=dim_action, dim_collision=dim_collision, dim_pose=dim_pose,
        #                                       save_folder=savedir, config=config)
    elif config.algo == 'pick_mse_qg_combination':
        policy = PickPolicyMSECombinationOfQg(dim_action=dim_action, dim_collision=dim_collision, dim_pose=dim_pose,
                                              save_folder=savedir, config=config)
    elif config.algo == 'imle_qg_combination':
        policy = PlacePolicyIMLECombinationOfQg(dim_action=dim_action, dim_collision=dim_collision, dim_pose=dim_pose,
                                                save_folder=savedir, config=config)
    else:
        raise NotImplementedError
    return policy
