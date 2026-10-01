"""FastAPI 依赖注入（规范 §B.5）：客户端一律经 lifespan 构建、Depends 注入。"""

from typing import Annotated

from fastapi import Depends, Request
from httpx import Client
from web3 import Web3

from app.core.chains import ChainSpec
from app.core.config import Settings, get_settings


def get_request_settings() -> Settings:
    return get_settings()


def get_request_chain(request: Request) -> ChainSpec:
    return request.app.state.chain


def get_request_web3(request: Request) -> Web3:
    return request.app.state.w3


def get_request_explorer(request: Request) -> Client:
    return request.app.state.explorer


def get_request_bundler(request: Request) -> Client:
    return request.app.state.bundler


SettingsDep = Annotated[Settings, Depends(get_request_settings)]
ChainDep = Annotated[ChainSpec, Depends(get_request_chain)]
Web3Dep = Annotated[Web3, Depends(get_request_web3)]
ExplorerDep = Annotated[Client, Depends(get_request_explorer)]
BundlerDep = Annotated[Client, Depends(get_request_bundler)]
