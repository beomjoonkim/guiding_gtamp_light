from keras.layers import *
from keras.layers.merge import Concatenate
from keras.models import Model

from PlacePolicyMSE import PlacePolicyMSE

import tensorflow as tf


class PlacePolicyMSEFeedForward(PlacePolicyMSE):
    def __init__(self, dim_action, dim_collision, dim_pose, save_folder, config):
        PlacePolicyMSE.__init__(self, dim_action, dim_collision, dim_pose, save_folder, config)
        self.weight_file_name = 'place_mse_ff_seed_%d' % config.seed

    def construct_policy_output(self):
        self.goal_flag_input = Input(shape=(4,), name='goal_flag',
                                     dtype='float32')
        key_config_input = Flatten()(self.key_config_input)
        collision_input = Flatten()(self.collision_input)
        concat_input = Concatenate(axis=1)([collision_input, key_config_input])

        dense_num = 64
        hidden_action = Dense(dense_num, activation='relu',
                              kernel_initializer=self.kernel_initializer,
                              bias_initializer=self.bias_initializer)(concat_input)
        hidden_action = Dense(dense_num, activation='relu',
                              kernel_initializer=self.kernel_initializer,
                              bias_initializer=self.bias_initializer)(hidden_action)
        action_output = Dense(self.dim_action,
                              activation='linear',
                              kernel_initializer=self.kernel_initializer,
                              bias_initializer=self.bias_initializer,
                              name='policy_output')(hidden_action)
        return action_output

    def construct_policy_model(self):
        mse_model = Model(inputs=[self.key_config_input, self.collision_input, self.pose_input, self.noise_input],
                          outputs=self.policy_output,
                          name='policy_output')

        def custom_mse(y_true, y_pred):
            return tf.reduce_mean(tf.norm(y_true - y_pred, axis=-1))
        mse_model.compile(loss=custom_mse, optimizer=self.opt_D)
        return mse_model


