import time
import numpy as np
import pickle
import uuid
import torch

from gtamp_utils import utils


class Generator:
    def __init__(self, abstract_state, abstract_action, sampler, n_parameters_to_try_motion_planning, n_iter_limit,
                 problem_env, reachability_clf=None):
        self.abstract_state = abstract_state
        self.abstract_action = abstract_action
        self.sampler = sampler
        self.n_parameters_to_try_motion_planning = n_parameters_to_try_motion_planning
        self.n_iter_limit = n_iter_limit
        self.problem_env = problem_env
        self.feasible_pick_params = {}
        self.feasibility_checker = self.get_feasibility_checker()
        self.tried_samples = []
        self.tried_sample_labels = []
        # Convention:
        #   -3 for pick_basic_infeasible, -2 for place_basic_infeasible,
        #   -1 for pick_mp_infeasible, 0 for place_infeasible, 1 for place_feasible
        self.reachability_clf = reachability_clf

        # below are used for evaluating different samplers
        self.n_ik_checks = 0
        self.n_mp_checks = 0
        self.n_mp_infeasible = 0
        self.n_ik_infeasible = 0

    def get_feasibility_checker(self):
        raise NotImplementedError

    def sample_next_point(self, dont_check_motion_existence=False):
        target_obj = self.abstract_action.discrete_parameters['object']
        if target_obj in self.feasible_pick_params:
            self.feasibility_checker.feasible_pick = self.feasible_pick_params[target_obj]

        feasible_op_parameters, status = self.sample_feasible_op_parameters()
        if status == "NoSolution":
            return {'is_feasible': False}

        # We would have to move these to the loop in order to be fair
        if dont_check_motion_existence:
            chosen_op_param = self.choose_one_of_params(feasible_op_parameters, status)
        else:
            chosen_op_param = self.get_param_with_feasible_motion_plan(feasible_op_parameters)
        return chosen_op_param

    def sample_feasible_op_parameters(self):
        assert self.n_iter_limit > 0
        feasible_op_parameters = []
        feasibility_check_time = 0
        stime = time.time()
        while True:
            self.n_ik_checks += 1
            sampled_op_parameters = self.sampler.sample()

            stime2 = time.time()
            op_parameters, status = self.feasibility_checker.check_feasibility(self.abstract_action,
                                                                               sampled_op_parameters)
            feasibility_check_time += time.time() - stime2

            if status == 'HasSolution':
                self.tried_samples.append(np.hstack([op_parameters['pick']['action_parameters'],
                                                     op_parameters['place']['action_parameters']]))
                self.tried_sample_labels.append(-1)  # tentative label
                feasible_op_parameters.append(op_parameters)

                if len(feasible_op_parameters) >= self.n_parameters_to_try_motion_planning:
                    break
            else:
                self.tried_samples.append(sampled_op_parameters)
                # Why did it fail? Is it because of pick or place?
                if status == 'PickFailed':
                    self.tried_sample_labels.append(-3)
                elif status == 'PlaceFailed':
                    self.tried_sample_labels.append(-2)
        smpling_time = time.time() - stime
        print "IK time {:.5f}".format(smpling_time)
        if len(feasible_op_parameters) == 0:
            feasible_op_parameters.append(op_parameters)  # place holder
            status = "NoSolution"
        else:
            status = "HasSolution"

        return feasible_op_parameters, status

    @staticmethod
    def choose_one_of_params(candidate_parameters, status):
        sampled_feasible_parameters = status == "HasSolution"
        if sampled_feasible_parameters:
            chosen_op_param = candidate_parameters[0]
            chosen_op_param['motion'] = [chosen_op_param['q_goal']]
            chosen_op_param['is_feasible'] = True
        else:
            chosen_op_param = {'is_feasible': False}

        return chosen_op_param

    def get_param_with_feasible_motion_plan(self, candidate_parameters):
        n_feasible = len(candidate_parameters)
        n_mp_tried = 0

        for op in candidate_parameters:
            stime = time.time()
            self.n_mp_checks += 1
            param = np.hstack([op['pick']['action_parameters'], op['place']['action_parameters']])
            idx = np.where([np.all(np.isclose(param, p)) for p in self.tried_samples])[0][0]

            # todo why is there a mismatch betwen pick and place samples?
            print "n_mp_tried / n_feasible_params = %d / %d" % (n_mp_tried, n_feasible)
            chosen_pick_param = self.get_motion_plan([op['pick']])
            n_mp_tried += 1
            print "Motion planning time {:.5f}".format(time.time()-stime)

            if not chosen_pick_param['is_feasible']:
                print "Pick motion does not exist"
                self.tried_sample_labels[idx] = -1
                self.n_mp_infeasible += 1
                continue

            original_config = utils.get_body_xytheta(self.problem_env.robot).squeeze()
            utils.two_arm_pick_object(self.abstract_action.discrete_parameters['object'], chosen_pick_param)
            chosen_place_param = self.get_motion_plan([op['place']])  # calls MP
            utils.two_arm_place_object(chosen_pick_param)
            utils.set_robot_config(original_config)

            if chosen_place_param['is_feasible']:
                print 'Motion plan exists'
                self.tried_sample_labels[idx] = 1
                break
            else:
                self.tried_sample_labels[idx] = 0
                self.n_mp_infeasible += 1
                print "Place motion does not exist"

        if not chosen_pick_param['is_feasible']:
            print "Motion plan does not exist"
            return {'is_feasible': False}

        if not chosen_place_param['is_feasible']:
            print "Motion plan does not exist"
            return {'is_feasible': False}

        chosen_pap_param = {'pick': chosen_pick_param, 'place': chosen_place_param, 'is_feasible': True}
        return chosen_pap_param

    def get_motion_plan(self, candidate_parameters):
        motion_plan_goals = [op['q_goal'] for op in candidate_parameters]
        self.problem_env.motion_planner.algorithm = 'rrt'
        motion, status = self.problem_env.motion_planner.get_motion_plan(motion_plan_goals[0],
                                                                         source='sampler',
                                                                         n_iterations=[20, 50, 100, 500, 1000])
        self.problem_env.motion_planner.algorithm = 'prm'
        found_feasible_motion_plan = status == "HasSolution"

        if found_feasible_motion_plan:
            which_op_param = np.argmin(np.linalg.norm(motion[-1] - motion_plan_goals, axis=-1))
            chosen_op_param = candidate_parameters[which_op_param]
            chosen_op_param['motion'] = motion
            chosen_op_param['is_feasible'] = True
        else:
            chosen_op_param = candidate_parameters[0]
            chosen_op_param['is_feasible'] = False

        return chosen_op_param
