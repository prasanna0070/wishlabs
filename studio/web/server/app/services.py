"""Dependency container shared by the routes."""
from __future__ import annotations

from .agent import make_brain
from .builder import make_builder
from .builds import BuildManager
from .config import Settings
from .engine_bridge import EngineBridge
from .imagegen import ImageGen
from .projects import ProjectRepo
from .store import make_store
from .tts import NameVoice


class Services:
    def __init__(self, settings: Settings, store=None, builder=None, brain=None, imagegen=None):
        self.settings = settings
        self.store = store if store is not None else make_store(settings)
        self.repo = ProjectRepo(self.store)
        self.bridge = EngineBridge(settings)
        self.builder = builder if builder is not None else make_builder(settings, self.bridge, self.store)
        if imagegen is not None:
            self.imagegen = imagegen
        elif settings.agent == "scripted" or not settings.photos:
            self.imagegen = None
        else:
            self.imagegen = ImageGen(settings)
        self.builds = BuildManager(self)
        self.names = NameVoice(settings)
        self.brain = brain if brain is not None else make_brain(self)
