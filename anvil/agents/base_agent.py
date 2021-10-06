import os
from abc import ABC, abstractmethod
from logging import INFO, Logger
from typing import List, Optional, Type, Union

import numpy as np
import torch as T
from gym import Env
from torch.utils.tensorboard import SummaryWriter

from anvil.buffers.base_buffer import BaseBuffer
from anvil.buffers.rollout_buffer import RolloutBuffer
from anvil.callbacks.base_callback import BaseCallback
from anvil.common.type_aliases import Log, OptimizerSettings, Tensor
from anvil.common.utils import get_device, torch_to_numpy
from anvil.explorers import BaseExplorer
from anvil.models.actor_critics import ActorCritic
from anvil.updaters.actors import BaseActorUpdater
from anvil.updaters.critics import BaseCriticUpdater


class BaseAgent(ABC):
    def __init__(
        self,
        env: Env,
        model: ActorCritic,
        actor_updater_class: Type[BaseActorUpdater],
        critic_updater_class: Type[BaseCriticUpdater],
        buffer_class: Type[BaseBuffer],
        buffer_size: int,
        actor_optimizer_settings: OptimizerSettings = OptimizerSettings(),
        critic_optimizer_settings: OptimizerSettings = OptimizerSettings(),
        action_explorer_class: Optional[Type[BaseExplorer]] = None,
        callbacks: Optional[List[Type[BaseCallback]]] = None,
        device: Union[T.device, str] = "auto",
        verbose: bool = True,
        model_path: Optional[str] = None,
        tensorboard_log_path: Optional[str] = None,
        n_envs: int = 1,
    ) -> None:
        self.env = env
        self.model = model
        self.verbose = verbose
        self.model_path = model_path
        self.n_envs = n_envs
        self.buffer_size = buffer_size
        self.device = get_device(device)

        self.buffer = buffer_class(
            buffer_size=buffer_size,
            observation_space=env.observation_space,
            action_space=env.action_space,
            n_envs=n_envs,
            device=device,
        )

        self.logger = Logger(__name__, level=INFO)
        self.writer = SummaryWriter(tensorboard_log_path)
        # Load the model if a path is given
        if self.model_path is not None:
            self.load(model_path)

    def save(self, path: str):
        """Save the model"""
        path = path + ".pt"
        if self.verbose:
            self.logger.info(f"Saving weights to {path}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        T.save(self.model.state_dict(), path)

    def load(self, path: str):
        """Load the model"""
        path = path + ".pt"
        if self.verbose:
            self.logger.info(f"Loading weights from {path}")
        try:
            self.model.load_state_dict(T.load(path))
        except FileNotFoundError:
            if self.verbose:
                self.logger.info(
                    "File not found, assuming no model dict was to be loaded"
                )

    def _write_log(self, log: Log, step: int) -> None:
        self.writer.add_scalar("reward", log.reward, step)
        self.writer.add_scalar("actor_loss", log.actor_loss, step)
        self.writer.add_scalar("critic_loss", log.critic_loss, step)
        if log.kl_divergence is not None:
            self.writer.add_scalar("kl_divergence", log.kl_divergence, step)
        if log.entropy is not None:
            self.writer.add_scalar("entropy", log.entropy, step)

        self.logger.info(f"{step}: {log}")

    def predict(self, observations: Tensor) -> T.Tensor:
        return self.model(observations)

    def get_action_distribution(
        self, observations: Tensor
    ) -> T.distributions.Distribution:
        return self.model.get_action_distribution(observations)

    def critic(self, observations: Tensor, actions: Tensor) -> T.Tensor:
        return self.model.critic(observations, actions)

    def step_env(self, observation: np.ndarray, num_steps: int = 1) -> np.ndarray:
        for _ in range(num_steps):
            if self.action_explorer is not None:
                action = self.action_explorer(observation)
            else:
                action = self.model(observation)
            reward, next_observation, done, _ = self.env.step()
            self.buffer.add_trajectory(
                observation, torch_to_numpy(action), reward, next_observation, done
            )
            if done:
                observation = self.env.reset()
            else:
                observation = next_observation
        return observation

    @abstractmethod
    def _fit(
        self, batch_size: int, actor_epochs: int = 1, critic_epochs: int = 1
    ) -> Log:
        """Train the agent in the environment"""

    def fit(
        self,
        num_steps: int,
        batch_size: int,
        actor_epochs: int = 1,
        critic_epochs: int = 1,
    ) -> None:
        """
        Train the agent in the environment

        :param num_steps: total number of environment steps to train over
        :param samples_to_collect: the total number of samples to add to the buffer before a training step
        :param batch_size: minibatch size to make a single gradient descent step on
        :param actor_epochs: how many times to update the actor network in each training step
        :param critic_epochs: how many times to update the critic network in each training step
        """
        # Assume RolloutBuffer is used with on-policy agents, so translate env steps to training steps
        if isinstance(self.buffer, RolloutBuffer):
            num_steps = num_steps // self.buffer_size

        observation = self.env.reset()
        for step in range(num_steps):
            # Always fill buffer with enough samples for first training step
            if step == 0:
                observation = self.step_env(
                    observation=observation, num_steps=batch_size
                )
            # For on-policy, fill buffer and get minibatch samples over epochs
            elif isinstance(self.buffer, RolloutBuffer):
                observation = self.step_env(
                    observation=observation, num_steps=self.buffer_size
                )
            # For off-policy only a single step is done since old samples can be reused
            else:
                observation = self.step_env(observation=observation)
            log = self._fit(
                batch_size=batch_size,
                actor_epochs=actor_epochs,
                critic_epochs=critic_epochs,
            )
            log.reward = np.mean(self.buffer.last(batch_size=batch_size).rewards)
            self._write_log(log, step)
