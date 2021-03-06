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


class PlacePolicyMSESingleConvNet(PlacePolicyMSE):
    def __init__(self, dim_action, dim_collision, save_folder, tau, config):
        PlacePolicyMSE.__init__(self, dim_action, dim_collision, save_folder, tau, config)
        self.weight_file_name = 'place_mse_single_convnet_seed_%d' % config.seed
        self.loss_model = self.construct_loss_model()

    def construct_loss_model(self):
        def avg_distance_to_colliding_key_configs(x):
            policy_output = x[0]
            key_configs = x[1]
            diff = policy_output[:, :, 0:2] - key_configs[:, :, 0:2]
            distances = tf.norm(diff, axis=-1)  # ? by 291 by 1

            collisions = x[2]
            collisions = collisions[:, :, 0]
            collisions = tf.squeeze(collisions, axis=-1)
            n_cols = tf.reduce_sum(collisions, axis=1)  # ? by 291 by 1

            hinge_on_given_dist_limit = tf.maximum(1 - distances, 0)
            hinged_dists_to_colliding_configs = tf.multiply(hinge_on_given_dist_limit, collisions)
            return tf.reduce_sum(hinged_dists_to_colliding_configs, axis=-1) / n_cols

        repeated_poloutput = RepeatVector(self.n_key_confs)(self.policy_output)
        konf_input = Reshape((self.n_key_confs, 4))(self.key_config_input)
        diff_output = Lambda(avg_distance_to_colliding_key_configs, name='collision_distance_output')(
            [repeated_poloutput, konf_input, self.collision_input])

        model = Model(inputs=[self.goal_flag_input, self.key_config_input, self.collision_input, self.pose_input,
                              self.noise_input],
                      outputs=[self.policy_output],
                      name='loss_model')

        def custom_mse(y_true, y_pred):
            return tf.reduce_mean(tf.norm(y_true - y_pred, axis=-1))

        # model.compile(loss=[lambda _, pred: pred, 'mse'], optimizer=self.opt_D, loss_weights=[0, 1])
        model.compile(loss='mse', optimizer=self.opt_D)
        return model

    def construct_policy_output(self):
        eval_net = self.construct_eval_net()
        key_config_input = Reshape((self.n_key_confs, 4))(self.key_config_input)
        best_qk = Lambda(lambda x: K.batch_dot(x[0], x[1]), name='best_qk')([eval_net, key_config_input])

        output = self.construct_qg_output(eval_net)
        return output

    def construct_eval_net(self):
        pose_input = RepeatVector(self.n_key_confs)(self.pose_input)
        pose_input = Reshape((self.n_key_confs, self.dim_poses, 1))(pose_input)

        concat_input = Concatenate(axis=2)([pose_input, self.collision_input])

        n_dim = concat_input.shape[2]._value
        n_filters = 32
        H = Conv2D(filters=n_filters,
                   kernel_size=(1, n_dim),
                   strides=(1, 1),
                   activation='relu',
                   kernel_initializer=self.kernel_initializer,
                   bias_initializer=self.bias_initializer)(concat_input)
        H = Conv2D(filters=n_filters,
                   kernel_size=(4, 1),
                   strides=(2, 1),
                   activation='relu',
                   kernel_initializer=self.kernel_initializer,
                   bias_initializer=self.bias_initializer)(H)
        value = Conv2D(filters=32,
                       kernel_size=(4, 1),
                       strides=(2, 1),
                       activation='relu',
                       kernel_initializer=self.kernel_initializer,
                       bias_initializer=self.bias_initializer)(H)
        value = Flatten()(value)
        value = Dense(self.n_key_confs, activation='linear',
                      kernel_initializer=self.kernel_initializer,
                      bias_initializer=self.bias_initializer)(value)
        model = self.construct_model(value, 'value_model')

        def compute_softmax(x):
            return K.softmax(x, axis=-1)

        evalnet = Lambda(compute_softmax, name='softmax')(value)
        evalnet = Reshape((self.n_key_confs,))(evalnet)

        return evalnet

    def construct_qg_output(self, best_qk):
        concat = Concatenate(axis=-1)([self.pose_input, best_qk])
        value = Dense(32, activation='relu',
                      kernel_initializer=self.kernel_initializer,
                      bias_initializer=self.bias_initializer)(concat)
        value = Dense(32, activation='relu',
                      kernel_initializer=self.kernel_initializer,
                      bias_initializer=self.bias_initializer)(value)
        value = Dense(4, activation='linear',
                      kernel_initializer=self.kernel_initializer,
                      bias_initializer=self.bias_initializer, name='policy_output')(value)
        return value

    def construct_policy_model(self):
        mse_model = Model(inputs=[self.goal_flag_input, self.key_config_input, self.collision_input, self.pose_input,
                                  self.noise_input],
                          outputs=self.policy_output,
                          name='policy_output')
        mse_model.compile(loss='mse', optimizer=self.opt_D)
        return mse_model

    def train_policy(self, states, konf_relevance, poses, rel_konfs, goal_flags, actions, sum_rewards, epochs=500):
        train_idxs, test_idxs = self.get_train_and_test_indices(len(actions))
        train_data, test_data = self.get_train_and_test_data(states, konf_relevance, poses, rel_konfs, goal_flags,
                                                             actions, sum_rewards,
                                                             train_idxs, test_idxs)
        callbacks = self.create_callbacks_for_training()

        actions = train_data['actions']
        goal_flags = train_data['goal_flags']
        poses = train_data['poses']
        rel_konfs = train_data['rel_konfs']
        collisions = train_data['states']
        noise_smpls = noise(z_size=(len(actions), self.dim_noise))
        inp = [goal_flags, rel_konfs, collisions, poses, noise_smpls]
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
