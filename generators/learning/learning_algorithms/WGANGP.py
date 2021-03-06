import torch
import torch.autograd as autograd
import torch.optim as optim
import numpy as np
from sklearn.neighbors import KernelDensity
import os
import scipy as sp

from torch import nn
from gtamp_utils import utils


class Discriminator(nn.Module):
    def __init__(self, dim_data):
        nn.Module.__init__(self)
        n_hidden = 32
        n_konfs = 618 * 2
        self.konf_net = \
            nn.Sequential(
                torch.nn.Linear(n_konfs, n_hidden),
                nn.ReLU(),
                torch.nn.Linear(n_hidden, n_hidden),
                nn.ReLU()
            )

        dim_poses = 24
        self.pose_net = \
            nn.Sequential(
                torch.nn.Linear(dim_poses, n_hidden),
                nn.ReLU(),
                torch.nn.Linear(n_hidden, n_hidden),
                nn.ReLU()
            )

        dim_actions = dim_data
        self.action_net = \
            nn.Sequential(
                torch.nn.Linear(dim_actions, n_hidden),
                nn.ReLU(),
                torch.nn.Linear(n_hidden, n_hidden),
                nn.ReLU()
            )

        dim_input = n_hidden * 3
        self.output = \
            nn.Sequential(
                torch.nn.Linear(dim_input, n_hidden),
                nn.ReLU(),
                torch.nn.Linear(n_hidden, 1)
            )

    def forward(self, action, konf, pose):
        konf = konf.view((-1, 618 * 2))
        konf_val = self.konf_net(konf)
        pose_val = self.pose_net(pose)
        action_val = self.action_net(action)
        concat = torch.cat((konf_val, pose_val, action_val), -1)
        return self.output(concat)


class Generator(nn.Module):
    def __init__(self, dim_data):
        nn.Module.__init__(self)
        n_hidden = 32
        n_konfs = 618 * 2
        self.konf_net = \
            nn.Sequential(
                torch.nn.Linear(n_konfs, n_hidden),
                nn.ReLU(),
                torch.nn.Linear(n_hidden, n_hidden),
                nn.ReLU()
            )

        dim_poses = 24
        self.pose_net = \
            nn.Sequential(
                torch.nn.Linear(dim_poses, n_hidden),
                nn.ReLU(),
                torch.nn.Linear(n_hidden, n_hidden),
                nn.ReLU()
            )

        dim_actions = dim_data
        dim_input = n_hidden * 2 + dim_actions
        self.output = \
            nn.Sequential(
                torch.nn.Linear(dim_input, n_hidden),
                nn.ReLU(),
                torch.nn.Linear(n_hidden, dim_actions)
            )

    def forward(self, konf, pose, noise):
        konf = konf.view((-1, 618 * 2))
        konf_val = self.konf_net(konf)
        pose_val = self.pose_net(pose)
        concat = torch.cat((konf_val, pose_val, noise), -1)

        return self.output(concat)


def calc_gradient_penalty(discriminator, actions_v, konf_obsts_v, poses_v, fake_data, batch_size):
    lambda_val = .1  # Smaller lambda seems to help for toy tasks specifically

    alpha = torch.rand(len(actions_v), 1)
    alpha = alpha.expand(actions_v.size())
    alpha = alpha

    interpolates = alpha * actions_v + ((1 - alpha) * fake_data)

    interpolates = autograd.Variable(interpolates, requires_grad=True)

    disc_interpolates = discriminator(interpolates, konf_obsts_v, poses_v)

    gradients = autograd.grad(outputs=disc_interpolates, inputs=interpolates,
                              grad_outputs=torch.ones(disc_interpolates.size()),
                              create_graph=True, retain_graph=True, only_inputs=True)[0]

    gradient_penalty = ((gradients.norm(2, dim=1) - 1) ** 2).mean() * lambda_val
    return gradient_penalty


class WGANgp:
    def __init__(self, action_type, region_name):
        self.action_type = action_type
        self.n_dim_actions = self.get_dim_action(action_type)

        self.discriminator = Discriminator(self.n_dim_actions)
        self.generator = Generator(self.n_dim_actions)
        self.region_name = region_name
        self.weight_dir = self.get_weight_dir(action_type, region_name)
        self.domain = self.get_domain(action_type, region_name)

        if not os.path.isdir(self.weight_dir):
            os.makedirs(self.weight_dir)

    @staticmethod
    def get_dim_action(action_type):
        if 'pick' in action_type:
            return 7
        else:
            return 4

    @staticmethod
    def get_weight_dir(action_type, region_name):
        if 'place' in action_type:
            dir = './generators/learning/learned_weights/{}/{}/wgangp/'.format(action_type, region_name)
        else:
            dir = './generators/learning/learned_weights/{}/wgangp/'.format(action_type)
        return dir

    def get_domain(self, action_type, region_name):
        if 'place' in action_type:
            if region_name == 'loading_region':
                domain = np.array(
                    [[-0.34469225, -8.14641946, -1., -0.99999925], [3.92354742, -5.25567767, 1., 0.99999993]])
            else:
                domain = np.array(
                    [[-1.28392928, -2.95494754, -0.99999998, -0.99999999], [5.01948716, 2.58819546, 1., 1.]])
        else:
            domain = utils.get_pick_domain()
            portion, base_angle, facing_angle_offset = domain[0, 3:]
            grasp_params = domain[0, 0:3]
            base_angle = utils.encode_angle_in_sin_and_cos(base_angle)
            min_domain = np.hstack([grasp_params, portion, base_angle, facing_angle_offset])
            min_domain[4:6] = np.array([-1, -1])

            portion, base_angle, facing_angle_offset = domain[1, 3:]
            grasp_params = domain[1, 0:3]
            base_angle = utils.encode_angle_in_sin_and_cos(base_angle)
            max_domain = np.hstack([grasp_params, portion, base_angle, facing_angle_offset])
            max_domain[4:6] = np.array([1, 1])

            domain = np.vstack([min_domain, max_domain])

        return domain

    def generate(self, konf_obsts, poses):
        noise = torch.randn(len(konf_obsts), self.n_dim_actions)
        konf_obsts = torch.Tensor(konf_obsts)
        poses = torch.Tensor(poses)
        samples = self.generator(konf_obsts, poses, noise).cpu().data.numpy()
        return samples

    def load_weights(self, iteration, verbose=True):
        weight_file = self.weight_dir + '/gen_iter_%d.pt' % iteration
        if verbose:
            print "Loading weight file", weight_file
        self.generator.load_state_dict(torch.load(weight_file))
        weight_file = self.weight_dir + '/disc_iter_%d.pt' % iteration
        try:
            self.discriminator.load_state_dict(torch.load(weight_file))
        except IOError:
            pass

    def evaluate_generator(self, test_data, iteration=None):
        is_load_weights = iteration is not None
        if is_load_weights:
            self.load_weights(iteration)

        test_data = test_data[:]
        poses = torch.from_numpy(test_data['poses']).float()
        konf_obsts = torch.from_numpy(test_data['konf_obsts']).float()

        n_data = len(poses)
        n_smpls_per_state = 100
        smpls = []
        for _ in range(n_smpls_per_state):
            noise = torch.randn(n_data, self.n_dim_actions)
            new_smpls = self.generator(konf_obsts, poses, noise)
            smpls.append(new_smpls)
        smpls = torch.stack(smpls)

        real_actions = test_data['actions']
        real_mean = real_actions.mean(axis=0)
        real_std = real_actions.std(axis=0)
        real_actions = (real_actions - real_mean) / real_std
        real_data_scores = []
        entropies = []
        min_mses = []
        for idx in range(n_data):
            smpls_from_state = smpls[:, idx, :]
            smpls_from_state = smpls_from_state.cpu().detach().numpy()
            smpls_from_state = (smpls_from_state - real_mean) / real_std
            real_action = real_actions[idx].reshape(-1, self.n_dim_actions)

            # measure the MSE
            dists = ((real_action * real_std + real_mean) - (smpls_from_state * real_std + real_mean)) ** 2
            dists = np.sqrt(np.sum(dists, axis=-1))
            min_mse = min(dists)
            min_mses.append(min_mse)

            # fit the KDE
            generated_model = KernelDensity(kernel='gaussian', bandwidth=0.1).fit(smpls_from_state)
            real_data_scores.append(generated_model.score(real_action))

            # measure the entropy
            smpls_from_state = smpls_from_state * real_std + real_mean
            if 'pick' in self.action_type:
                base_angles = smpls_from_state[:, 4:6]
                H, _, _ = np.histogram2d(base_angles[:, 0], base_angles[:, 1], bins=10,
                                         range=self.domain[:, 4:6].transpose())
            else:
                place_x, place_y = smpls_from_state[:, 0], smpls_from_state[:, 1]
                H, _, _ = np.histogram2d(place_x, place_y, bins=10, range=self.domain[:, 0:2].transpose())
            all_smpls_out_of_range = np.sum(H) == 0
            if all_smpls_out_of_range:
                entropy = np.inf
            else:
                prob = H / np.sum(H)
                entropy = sp.stats.entropy(prob.flatten())
            entropies.append(entropy)

        return np.mean(min_mses), np.mean(real_data_scores), np.mean(entropies)

    def train(self, data_loader, test_set, n_train):
        batch_size = 32  # Batch size

        optimizerD = optim.Adam(self.discriminator.parameters(), lr=1e-4, betas=(0.5, 0.9))
        optimizerG = optim.Adam(self.generator.parameters(), lr=1e-4, betas=(0.5, 0.9))

        CRITIC_ITERS = 5  # How many critic iterations per generator iteration
        use_cuda = False

        one = torch.FloatTensor([1])
        mone = one * -1
        if use_cuda:
            one = one.cuda()
            mone = mone.cuda()

        n_data_dim = self.n_dim_actions
        total_n_data = n_train
        total_iterations = 1000 * (total_n_data + 1)

        def data_generator():
            while True:
                for d in data_loader:
                    yield d

        data_gen = data_generator()

        for iteration in xrange(total_iterations):
            ############################
            # (1) Update D network
            ###########################
            for p in self.discriminator.parameters():  # reset requires_grad
                p.requires_grad = True  # they are set to False below in self.generator update

            for iter_d in xrange(CRITIC_ITERS):
                _data = data_gen.next()
                poses = _data['poses'].float()
                konf_obsts = _data['konf_obsts'].float()
                actions = _data['actions'].float()
                if use_cuda:
                    poses = poses.cuda()
                    konf_obsts = konf_obsts.cuda()
                    actions = actions.cuda()
                poses_v = autograd.Variable(poses)
                konf_obsts_v = autograd.Variable(konf_obsts)
                actions_v = autograd.Variable(actions)

                self.discriminator.zero_grad()

                # train with real
                D_real = self.discriminator(actions_v, konf_obsts_v, poses_v)
                D_real = D_real.mean()
                D_real.backward(mone)

                # train with fake
                noise = torch.randn(len(poses), n_data_dim)
                if use_cuda:
                    noise = noise.cuda()
                noisev = autograd.Variable(noise, volatile=True)  # totally freeze self.generator
                fake = autograd.Variable(self.generator(konf_obsts_v, poses_v, noisev).data)
                inputv = fake
                D_fake = self.discriminator(inputv, konf_obsts_v, poses_v)
                D_fake = D_fake.mean()
                D_fake.backward(one)

                # train with gradient penalty
                gradient_penalty = calc_gradient_penalty(self.discriminator, actions_v.data, konf_obsts_v, poses_v,
                                                         fake.data, batch_size)
                gradient_penalty.backward()

                D_cost = D_fake - D_real + gradient_penalty
                Wasserstein_D = D_real - D_fake
                optimizerD.step()

            ############################
            # (2) Update G network
            ###########################
            _data = data_gen.next()
            poses = _data['poses'].float()
            konf_obsts = _data['konf_obsts'].float()
            actions = _data['actions'].float()
            if use_cuda:
                poses = poses.cuda()
                konf_obsts = konf_obsts.cuda()
                actions = actions.cuda()
            poses_v = autograd.Variable(poses)
            konf_obsts_v = autograd.Variable(konf_obsts)

            for p in self.discriminator.parameters():
                p.requires_grad = False  # to avoid computation
            self.generator.zero_grad()

            noise = torch.randn(len(poses), n_data_dim)
            if use_cuda:
                noise = noise.cuda()
            noisev = autograd.Variable(noise)
            fake = self.generator(konf_obsts_v, poses_v, noisev)
            G = self.discriminator(fake, konf_obsts_v, poses_v)
            G = G.mean()
            G.backward(mone)
            G_cost = -G
            optimizerG.step()

            # Write logs and save samples
            if iteration % 100 == 0:
                print "Iteration %d / %d" % (iteration, total_iterations)
                path = self.weight_dir + '/disc_iter_%d.pt' % iteration
                torch.save(self.discriminator.state_dict(), path)
                path = self.weight_dir + '/gen_iter_%d.pt' % iteration
                torch.save(self.generator.state_dict(), path)
