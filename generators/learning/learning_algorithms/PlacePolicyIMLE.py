from PlacePolicy import PlacePolicy
from keras.layers import *
from keras.models import Model
from keras.callbacks import *

from generators.learning.utils.data_processing_utils import action_data_mode
import numpy as np
import time
import tensorflow as tf


def gaussian_noise(z_size):
    return np.random.normal(size=z_size).astype('float32')


def uniform_noise(z_size):
    noise_dim = z_size[-1]
    return np.random.uniform([0] * noise_dim, [1] * noise_dim, size=z_size).astype('float32')


class PlacePolicyIMLE(PlacePolicy):
    def __init__(self, dim_action, dim_collision, dim_poses, n_key_configs, save_folder, config):
        self.weight_input = Input(shape=(1,), dtype='float32', name='weight_for_each_sample')
        PlacePolicy.__init__(self, dim_action, dim_collision, dim_poses, n_key_configs, save_folder, config)
        self.loss_model = self.construct_loss_model()

    def construct_policy_output(self):
        raise NotImplementedError

    def construct_policy_model(self):
        model = Model(inputs=[self.goal_flag_input, self.key_config_input, self.collision_input, self.pose_input,
                              self.noise_input],
                      outputs=[self.policy_output],
                      name='policy_model')
        model.compile(loss='mse', optimizer=self.opt_D)
        return model

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

        def distance_to_target_obj(x):
            target_obj_pose = x[0][:, 0:4]
            policy_output = x[1]
            diff = policy_output[:, 0:2] - target_obj_pose[:, 0:2]
            distances = tf.norm(diff, axis=-1)
            # Limits computed using pick domain and the robot arm length
            hinge_on_given_dist_limit = tf.maximum(distances-0.88596, 0) # must be within 0.9844 from the object.
            hinge_on_given_dist_limit2 = tf.maximum(0.4-distances, 0)  # must be 0.4 away from the object

            return tf.reduce_mean(hinge_on_given_dist_limit+hinge_on_given_dist_limit2)

        model = Model(inputs=[self.goal_flag_input, self.key_config_input, self.collision_input, self.pose_input,
                              self.noise_input],
                      outputs=[self.policy_output],
                      name='loss_model')

        def custom_mse(y_true, y_pred):
            return tf.reduce_mean(tf.norm(y_true - y_pred, axis=-1))

        model.compile(loss=[custom_mse], optimizer=self.opt_D)
        return model

    @staticmethod
    def get_batch_based_on_rewards(cols, goal_flags, poses, rel_konfs, actions, sum_rewards, batch_size):
        indices = np.random.randint(0, actions.shape[0], size=batch_size)

        n_data = actions.shape[0]
        probability_of_being_sampled = np.exp(sum_rewards) / np.sum(np.exp(sum_rewards))
        indices = np.random.choice(n_data, batch_size, p=probability_of_being_sampled)
        cols_batch = np.array(cols[indices, :])  # collision vector
        goal_flag_batch = np.array(goal_flags[indices, :])  # collision vector
        a_batch = np.array(actions[indices, :])
        pose_batch = np.array(poses[indices, :])
        konf_batch = np.array(rel_konfs[indices, :])
        sum_reward_batch = np.array(sum_rewards[indices, :])
        return cols_batch, goal_flag_batch, pose_batch, konf_batch, a_batch, sum_reward_batch

    def create_callbacks_for_training(self):
        callbacks = [
            TerminateOnNaN(),
            EarlyStopping(monitor='val_loss', min_delta=1e-4, patience=10),
            ModelCheckpoint(filepath=self.save_folder + self.weight_file_name,
                            verbose=False,
                            save_best_only=True,
                            save_weights_only=True),
        ]
        return callbacks

    def train_policy(self, states, poses, rel_konfs, goal_flags, actions, sum_rewards, epochs=1000):
        # todo factor this code
        train_idxs, test_idxs = self.get_train_and_test_indices(len(actions))
        train_data, test_data = self.get_train_and_test_data(states, poses, rel_konfs, goal_flags,
                                                             actions, sum_rewards,
                                                             train_idxs, test_idxs)

        t_actions = test_data['actions']
        t_goal_flags = test_data['goal_flags']
        t_poses = test_data['poses']
        t_rel_konfs = test_data['rel_konfs']
        t_collisions = test_data['states']

        n_test_data = len(t_collisions)

        data_resampling_step = 1
        num_smpl_per_state = 10

        actions = train_data['actions']
        goal_flags = train_data['goal_flags']
        poses = train_data['poses']
        rel_konfs = train_data['rel_konfs']
        collisions = train_data['states']
        callbacks = self.create_callbacks_for_training()

        gen_w_norm_patience = 10
        gen_w_norms = [-1] * gen_w_norm_patience
        valid_errs = []
        patience = 0
        for epoch in range(epochs):
            print 'Epoch %d/%d' % (epoch, epochs)
            is_time_to_smpl_new_data = epoch % data_resampling_step == 0
            batch_size = len(actions)
            # col_batch, goal_flag_batch, pose_batch, rel_konf_batch, a_batch, sum_reward_batch = \
            #    self.get_batch(collisions, goal_flags, poses, rel_konfs, actions, sum_rewards, batch_size=batch_size)
            goal_flag_batch = goal_flags
            col_batch = collisions
            pose_batch = poses
            rel_konf_batch = rel_konfs
            a_batch = actions

            stime = time.time()
            # train data
            world_states = (goal_flag_batch, rel_konf_batch, col_batch, pose_batch)
            noise_smpls = gaussian_noise(z_size=(batch_size, num_smpl_per_state, self.dim_noise))
            generated_actions = self.generate_k_smples_for_multiple_states(world_states, noise_smpls)
            chosen_noise_smpls = self.get_closest_noise_smpls_for_each_action(a_batch, generated_actions, noise_smpls)
            # validation data
            t_world_states = (t_goal_flags, t_rel_konfs, t_collisions, t_poses)
            t_noise_smpls = gaussian_noise(z_size=(n_test_data, num_smpl_per_state, self.dim_noise))
            t_generated_actions = self.generate_k_smples_for_multiple_states(t_world_states, t_noise_smpls)
            t_chosen_noise_smpls = self.get_closest_noise_smpls_for_each_action(t_actions, t_generated_actions,
                                                                                t_noise_smpls)
            print "Data generation time", time.time() - stime

            # I also need to tag on the Q-learning objective
            before = self.policy_model.get_weights()
            self.loss_model.fit([goal_flag_batch, rel_konf_batch, col_batch, pose_batch, chosen_noise_smpls],
                                [a_batch],
                                epochs=1000,
                                batch_size=32,
                                validation_data=(
                                    [t_goal_flags, t_rel_konfs, t_collisions, t_poses, t_chosen_noise_smpls],
                                    [t_actions]),
                                callbacks=callbacks,
                                verbose=True)
            # self.load_weights()
            after = self.policy_model.get_weights()
            gen_w_norm = np.linalg.norm(np.hstack([(a - b).flatten() for a, b in zip(before, after)]))
            print "Generator weight norm diff", gen_w_norm
            gen_w_norms[epoch % gen_w_norm_patience] = gen_w_norm

            pred = self.policy_model.predict([t_goal_flags, t_rel_konfs, t_collisions, t_poses, t_chosen_noise_smpls])
            valid_err = np.mean(np.linalg.norm(pred - t_actions, axis=-1))
            valid_errs.append(valid_err)
            if epoch % 20 == 0:
                self.save_weights('epoch_' + str(epoch))

            if valid_err <= np.min(valid_errs):
                self.save_weights(additional_name='best_val_err')
                patience = 0
            else:
                patience += 1

            if patience > 30:
                self.save_weights('epoch_' + str(epoch))
                break

            print "Val error %.2f patience %d" % (valid_err, patience)
            print np.min(valid_errs)
