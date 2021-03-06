from keras.layers import *
from keras.layers.merge import Concatenate
from generators.learning.PlacePolicyMSE import PlacePolicyMSE
from keras.models import Model
from keras import backend as K

import socket
import numpy as np


def noise(z_size):
    noise_dim = z_size[-1]
    return np.random.uniform([0] * noise_dim, [1] * noise_dim, size=z_size).astype('float32')
    # return np.random.normal(size=z_size).astype('float32')


if socket.gethostname() == 'lab' or socket.gethostname() == 'phaedra':
    ROOTDIR = './'
else:
    ROOTDIR = '/data/public/rw/pass.port/guiding_gtamp/'

import tensorflow as tf


class PlacePolicyMSECombinationOfQg(PlacePolicyMSE):
    def __init__(self, dim_action, dim_collision, dim_pose, n_key_configs, save_folder, config):
        PlacePolicyMSE.__init__(self, dim_action, dim_collision, dim_pose, n_key_configs, save_folder, config)
        self.weight_file_name = '%s_mse_qg_combination_seed_%d' % (config.atype, config.seed)
        self.loss_model = self.construct_loss_model()
        print "Created Self-attention Dense Gen Net Dense Eval Net"

    def construct_loss_model(self):
        model = Model(inputs=[self.key_config_input, self.collision_input, self.pose_input, self.noise_input],
                      outputs=[self.policy_output],
                      name='loss_model')

        def custom_mse(y_true, y_pred):
            return tf.reduce_mean(tf.norm(y_true - y_pred, axis=-1))

        model.compile(loss=custom_mse, optimizer=self.opt_D)
        return model

    def construct_policy_output(self):
        candidate_qg = self.construct_value_output()
        evalnet_input = Reshape((self.n_key_confs, 4, 1))(candidate_qg)
        eval_net = self.construct_eval_net(evalnet_input)
        self.evalnet_model = self.construct_model(eval_net, 'evalnet_model')
        output = Lambda(lambda x: K.batch_dot(x[0], x[1]), name='policy_output')([eval_net, candidate_qg])
        return output

    def construct_value_output(self):
        pose_input = RepeatVector(self.n_key_confs)(self.pose_input)
        pose_input = Reshape((self.n_key_confs, self.dim_poses, 1))(pose_input)

        concat_input = Concatenate(axis=2)([pose_input, self.key_config_input])

        n_dim = concat_input.shape[2]._value
        n_filters = 32
        H = Conv2D(filters=n_filters,
                   kernel_size=(1, n_dim),
                   strides=(1, 1),
                   activation='relu',
                   kernel_initializer=self.kernel_initializer,
                   bias_initializer=self.bias_initializer)(concat_input)
        for _ in range(2):
            H = Conv2D(filters=n_filters,
                       kernel_size=(1, 1),
                       strides=(1, 1),
                       activation='relu',
                       kernel_initializer=self.kernel_initializer,
                       bias_initializer=self.bias_initializer)(H)
        value = Conv2D(filters=self.dim_action,
                       kernel_size=(1, 1),
                       strides=(1, 1),
                       activation='linear',
                       kernel_initializer=self.kernel_initializer,
                       bias_initializer=self.bias_initializer)(H)

        value = Lambda(lambda x: K.squeeze(x, axis=2), name='candidate_qg')(value)
        self.value_model = Model(
            inputs=[self.goal_flag_input, self.key_config_input, self.pose_input, self.noise_input],
            outputs=value,
            name='value_model')
        return value

    def construct_eval_net(self, qg_candidates):
        pose_input = RepeatVector(self.n_key_confs)(self.pose_input)
        pose_input = Reshape((self.n_key_confs, self.dim_poses, 1))(pose_input)

        collision_inp = Flatten()(self.collision_input)
        collision_inp = RepeatVector(self.n_key_confs)(collision_inp)
        collision_inp = Reshape((self.n_key_confs, self.n_collisions * 2, 1))(collision_inp)
        concat_input = Concatenate(axis=2)([pose_input, qg_candidates, collision_inp])
        # concat_input = Concatenate(axis=2)([pose_input, qg_candidates])
        n_dim = concat_input.shape[2]._value
        dense_num = 32
        H = Conv2D(filters=dense_num,
                   kernel_size=(1, n_dim),
                   strides=(1, 1),
                   activation='relu',
                   kernel_initializer=self.kernel_initializer,
                   bias_initializer=self.bias_initializer)(concat_input)
        H = Conv2D(filters=dense_num,
                   kernel_size=(1, 1),
                   strides=(1, 1),
                   activation='relu',
                   kernel_initializer=self.kernel_initializer,
                   bias_initializer=self.bias_initializer)(H)
        H = Conv2D(filters=1,
                   kernel_size=(1, 1),
                   strides=(1, 1),
                   activation='linear',
                   kernel_initializer=self.kernel_initializer,
                   bias_initializer=self.bias_initializer)(H)
        H = Reshape((self.n_key_confs,))(H)

        def compute_softmax(x):
            return K.softmax(x, axis=-1)

        evalnet = Lambda(compute_softmax, name='softmax')(H)
        self.evalnet_model = self.construct_model(evalnet, 'evalnet_model')

        return evalnet

    def construct_policy_model(self):
        mse_model = Model(inputs=[self.key_config_input, self.collision_input, self.pose_input, self.noise_input],
                          outputs=self.policy_output,
                          name='policy_output')
        mse_model.compile(loss='mse', optimizer=self.opt_D)
        return mse_model

    def train_policy(self, states, poses, rel_konfs, goal_flags, actions, sum_rewards, epochs=500):
        train_idxs, test_idxs = self.get_train_and_test_indices(len(actions))
        train_data, test_data = self.get_train_and_test_data(states, poses, rel_konfs, goal_flags,
                                                             actions, sum_rewards,
                                                             train_idxs, test_idxs)
        callbacks = self.create_callbacks_for_training()

        actions = train_data['actions']
        goal_flags = train_data['goal_flags']
        poses = train_data['poses']
        rel_konfs = train_data['rel_konfs']
        collisions = train_data['states']
        noise_smpls = noise(z_size=(len(actions), self.dim_noise))
        inp = [rel_konfs, collisions, poses, noise_smpls]
        pre_mse = self.compute_policy_mse(test_data)
        self.loss_model.fit(inp, actions,
                            batch_size=32,
                            epochs=epochs,
                            verbose=2,
                            callbacks=callbacks,
                            validation_split=0.1, shuffle=False)
        # load the best model
        self.load_weights()
        post_mse = self.compute_policy_mse(test_data)
        print "Pre-and-post test errors", pre_mse, post_mse
