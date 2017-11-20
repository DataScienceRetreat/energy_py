import collections
import itertools
import logging
import os

import numpy as np
import pandas as pd

from energy_py import Utils


class Memory(Utils):
    """
    Purpose of this class is to
        store the experiences of the agent
        process experiences for use by the agent to act or learn from

    The memory of the agent is two lists of experience numpy arrays

    I use two lists because experience as recieved from the environment is
    often not suitable for a machine to learn from

    Most commonly we will need to attempt to standardize or normalize data 
    used with neural networks

    The two lists are
        self.experiences = data as observed
        self.machine_experiences = data for use by neural networks

    The two experience numpy arrays hold the following data
        experience = (observation,          0
                      action,               1
                      reward,               2
                      next_observation,     3
                      step,                 4
                      episode)              5

        machine_experience = (observation,       0
                              action,            1
                              reward,            2
                              next_observation,  3
                              step,              4
                              episode,           5
                              discounted_return) 6

      discounted_return is the true Monte Carlo return

    self.agent_stats is a dictionary that can be used to keep track of any
    statistics as generated by the agent
    """

    def __init__(self, 
                 observation_space,
                 action_space,
                 reward_space,
                 discount,
                 memory_length,
                 process_reward,
                 process_return):

        super().__init__()

        #  MDP info
        self.observation_space = observation_space
        self.action_space = action_space
        self.reward_space = reward_space
        self.discount = discount

        #  memory & processing info:w
        self.memory_length = memory_length
        self.process_reward = process_reward
        self.process_return = process_return

        self.reset()

    def reset(self):
        """
        Resets the two experiences lists and agent_stats
        """
        self.experiences = []
        self.machine_experiences = []
        self.agent_stats = collections.defaultdict(list)

    def add_experience(self, observation,
                             action,
                             reward,
                             next_observation,
                             step,
                             episode):
        """
        Adds a single step of experience to the two experiences lists

        args
            observation
            action
            reward
            next_observation
            step
            episode
        """
        logging.debug('adding experience for ep {} step {}'.format(episode, step))

        #  make the experience array
        exp = np.array([observation,
                        action,
                        reward,
                        next_observation,
                        step,
                        episode])

        #  make the machine experience array
        m_exp = self.make_machine_experience(exp)

        #  add experiences to the memory
        self.experiences.append(exp)
        self.machine_experiences.append(m_exp)

        assert len(self.experiences) == len(self.machine_experiences)

    def make_machine_experience(self, exp):
        """
        Transforms an experience array to a machine_experience array

        Discounted return not updated here as we might not know it yet!
        i.e. if the function is used within episode

        args
            exp (np.array): single experience
        """
        #  scale the observation and action 
        scaled_obs = self.scale_array(exp[0], self.observation_space)
        scaled_action = self.scale_array(exp[1], self.action_space)

        if self.process_reward == 'normalize':
            reward = self.normalize(exp[2],
                                    self.reward_space.low,
                                    self.reward_space.high)
            reward = reward.reshape(1, 1)

        else:
            reward = exp[2]

        #  this if statement is needed because for the terminal state
        #  the next observation = False
        #   as we use array for experiecen, we cant use boolean (dtype!)
        if exp[3].all() == -999999:
            scaled_next_obs = exp[3]
        else:
            scaled_next_obs = self.scale_array(exp[3], self.observation_space)

        #  making an array for the scaled experience
        scaled_exp = np.array([scaled_obs,
                               scaled_action,
                               reward,
                               scaled_next_obs,
                               exp[4],  # step
                               exp[5],  # episode number
                               None])   # the Monte Carlo return
        return scaled_exp

    def calculate_returns(self, episode_number):
        """
        Calculates the Monte Carlo discounted return for a single episode

        Because we need to wait until episode end this functionality is split
        from make_machine_experience

        Potential to use the normalizer objectt here (for the scaling of
        returns etc) TODO

        Potential to reuse some code from make_machine_experience

        args
            episode_number (int)
            normalize_return (str): determines method for scaling return
        """
        #  create array so we can mask later
        #  note that we take from machine_experiences
        all_experiences = np.array(self.machine_experiences)
        assert all_experiences.shape[0] == len(self.machine_experiences)

        #  use boolean indexing to get experiences from last episode
        episode_mask = [all_experiences[:, 5] == episode_number]
        episode_experiences = all_experiences[episode_mask]

        #  now we can calculate the Monte Carlo discounted return
        #  R = the return from s'
        R, returns = 0, []
        #  note that we reverse the list here
        for exp in episode_experiences[::-1]:
            r = exp[2]
            R = r + self.discount * R  # the Bellman equation
            returns.insert(0, R)

        #  turn into array, print out some statistics before we scale
        rtns = np.array(returns)
        logging.info('episode {}'.format(episode_number))
        logging.info('total returns before scl {:.2f}'.format(rtns.sum()))
        logging.info('mean returns before scl {:.2f}'.format(rtns.mean()))
        logging.debug('stdv returns before scl {:.2f}'.format(rtns.std()))

        #  few different options for how to scale the return
        #  all the statistics below are on a per episode basis
        #  scope to get more complex here by using entire memory TODO       
        #  also scope to use an object for the scaling (eventually)
        if self.process_return == 'scale_only':
            rtns = rtns / rtns.std()
        if self.process_return == 'mean_scale':
            rtns = (rtns - rtns.mean()) / (rtns.std())
        if self.process_return == 'min_max':
            rtns = (rtns - rtns.min()) / (rtns.max() - rtns.min())

        logging.info('total returns after scl {:.2f}'.format(rtns.sum()))
        logging.info('mean returns after scl {:.2f}'.format(rtns.mean()))

        #  now we have the episode returns
        #  we can fill in the returns each experience in machine_experience
        #  for this episode
        new_exps = []
        assert len(episode_experiences) == len(rtns)
        for exp, rtn in zip(episode_experiences, rtns):
            exp[6] = rtn
            new_exps.append(exp)

        #  idea is to mask an index array
        idx_array = np.arange(all_experiences.shape[0])
        assert idx_array.shape[0] == all_experiences.shape[0]
        episode_indicies = idx_array[episode_mask]

        #  we then use indicies to slice self.machine_experiences with our
        #  new experiences
        start = episode_indicies[0]
        end = episode_indicies[-1] + 1
        self.machine_experiences[start:end] = new_exps

    def get_episode_batch(self, episode_number, scaled_actions):
        """
        Gets the experiences for a given episode

        args
            episode_number (int)
            scaled_actions (boolan): whether or not to scale the actions

        returns
            observations (np.array): shape=(samples, self.observation_dim)
            actions (np.array): shape=(samples, self.action_dim)
            returns (np.array): shape=(samples, 1)
        """

        exps = np.array(self.experiences)
        mach_exps = np.array(self.machine_experiences)
        assert exps.shape[0] == len(self.machine_experiences)

        #  use boolean indexing to get experiences from last episode
        episode_mask = [mach_exps[:, 5] == episode_number]
        episode_exps = exps[episode_mask]
        episode_mach_exps = mach_exps[episode_mask]

        observations, actions, returns = [], [], []
        for exp, mach_exp in zip(episode_exps, episode_mach_exps): 
            observations.append(mach_exp[0])

            #  policy gradients require logprob(action) - not scaled action
            #  so build in the option to get the actual action
            if scaled_actions:
                act = mach_exp[1] 
            else:
                act = exp[1]
            actions.append(act)

            returns.append(mach_exp[6])

        observations = np.array(observations).reshape(-1, len(self.observation_space))
        actions = np.array(actions).reshape(-1, len(self.action_space))
        returns = np.array(returns).reshape(-1, 1)

        assert observations.shape[0] == actions.shape[0]
        assert observations.shape[0] == returns.shape[0]

        assert not np.any(np.isnan(observations))
        assert not np.any(np.isnan(actions))
        assert not np.any(np.isnan(returns))

        return observations, actions, returns

    def get_random_batch(self, batch_size, save_batch=False):
        """
        Gets a random batch of experiences

        args
            batch_size (int)

        returns
            observations (np.array)
            actions (np.array)
            rewards (np.array)
            next_observations (np.array)
        """
        sample_size = min(batch_size, len(self.machine_experiences))

        #  limiting to the memory length
        mach_memory = self.machine_experiences[-self.memory_length:]

        #  indicies for the batch
        indicies = np.random.randint(low=0,
                                     high=len(mach_memory),
                                     size=sample_size)

        #  randomly sample from the memory & returns
        mach_exp_batch = [mach_memory[i] for i in indicies]

        obs = [exp[0] for exp in mach_exp_batch]
        acts = [exp[1] for exp in mach_exp_batch]
        rwrds = [exp[2] for exp in mach_exp_batch]
        next_obs = [exp[3] for exp in mach_exp_batch]

        #  space lengths used for reshaping
        obs_space_dim = len(self.observation_space)
        act_space_dim = len(self.action_space)

        obs = np.array(obs).reshape(sample_size, obs_space_dim)
        actions = np.array(acts).reshape(sample_size, act_space_dim)
        rewards = np.array(rwrds).reshape(sample_size, 1)
        next_obs = np.array(next_obs).reshape(sample_size, obs_space_dim)

        assert obs.shape[0] == actions.shape[0]
        assert obs.shape[0] == rewards.shape[0]
        assert obs.shape[0] == next_obs.shape[0]

        assert not np.any(np.isnan(obs))
        assert not np.any(np.isnan(actions))
        assert not np.any(np.isnan(rewards))
        assert not np.any(np.isnan(next_obs))

        return obs, actions, rewards, next_obs

    def output_results(self):
        """
        Extract data from the memory

        returns
            output_dict (dict): keys=name, values=pd.Series or pd.DataFrame
        """
        #  create lists on a step by step basis
        print('agent memory is making dataframes')
        assert len(self.experiences) == len(self.machine_experiences)

        ep, stp, obs, act, rew, nxt_obs = [], [], [], [], [], []
        mach_obs, mach_act, mach_rew, mach_nxt_obs, dis_ret = [], [], [], [], []
        for exp, mach_exp in itertools.zip_longest(self.experiences, self.machine_experiences):
            obs.append(exp[0])
            act.append(exp[1])
            rew.append(exp[2])
            nxt_obs.append(exp[3])
            stp.append(exp[4])
            ep.append(exp[5])

            mach_obs.append(mach_exp[0])
            mach_act.append(mach_exp[1])
            mach_rew.append(mach_exp[2])
            mach_nxt_obs.append(mach_exp[3])
            dis_ret.append(mach_exp[6])

        df_dict = {
                   'episode':ep,
                   'step':stp,
                   'observation':obs,
                   'action':act,
                   'reward':rew,
                   'next_observation':nxt_obs,
                   'scaled_reward':mach_rew,
                   'discounted_return':dis_ret,
                   'scaled_observation':mach_obs,
                   'scaled_action':mach_act,
                   'scaled_reward':mach_rew,
                   'scaled_next_observation':mach_nxt_obs,
                   }

        #  make a dataframe on a step by step basis
        df_stp = pd.DataFrame.from_dict(df_dict)

        #  make a dataframe on an episodic basis
        df_ep = df_stp.groupby(by=['episode'], axis=0).sum()

        #  set the index on the step df
        df_stp.set_index('episode', drop=True, inplace=True)

        #  add in the maximum cumulative reward
        df_ep.loc[:, 'cum_max_reward'] = df_ep.loc[:, 'reward'].cummax()

        #  add in the rolling average reward
        window = max(int(df_ep.shape[0]*0.1),2)

        df_ep.loc[:, 'rolling_mean'] = df_ep.loc[:, 'reward'].rolling(window=window,
                                                                      min_periods=1,
                                                                      center=False).mean()

        #  iterate over the agent_stats dictionary
        #  this can contain data with different indicies
        #  so we create one df per data
        #  and store these dfs in a dictionary
        agent_stats = {}
        for var, data in self.agent_stats.items():
            logging.info('making data frame for {} from agent_stats'.format(var))
            agent_stats[var] = pd.Series(data, name=var)

        output_dict = {'dataframe_steps' : df_stp,
                       'dataframe_episodic' : df_ep,
                       'agent_stats': agent_stats}
        return output_dict
