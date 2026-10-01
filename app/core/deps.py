"""FastAPI 依赖注入（规范 §B.5）：客户端一律经 lifespan 构建、Depends 注入。"""

from typing import Annotated

from fastapi import Depends, Request
from httpx import Client
from web3 import Web3

from app.core.chains import ChainSpec
from app.core.config import Settings, get_settings
from app.core.explorer import ExplorerClient
from app.core.keystore import Keystore
from app.core.tx import TxService


def get_request_settings() -> Settings:
    return get_settings()


def get_request_chain(request: Request) -> ChainSpec:
    return request.app.state.chain


def get_request_web3(request: Request) -> Web3:
    return request.app.state.w3


def get_request_keystore(request: Request) -> Keystore:
    return request.app.state.keystore


def get_request_tx(request: Request) -> TxService:
    return request.app.state.tx


def get_request_explorer(request: Request) -> ExplorerClient:
    return request.app.state.explorer_api


def get_request_explorer_http(request: Request) -> Client:
    return request.app.state.explorer_http


def get_request_bundler(request: Request) -> Client:
    return request.app.state.bundler


SettingsDep = Annotated[Settings, Depends(get_request_settings)]
ChainDep = Annotated[ChainSpec, Depends(get_request_chain)]
Web3Dep = Annotated[Web3, Depends(get_request_web3)]
KeystoreDep = Annotated[Keystore, Depends(get_request_keystore)]
TxServiceDep = Annotated[TxService, Depends(get_request_tx)]
ExplorerDep = Annotated[ExplorerClient, Depends(get_request_explorer)]
ExplorerHttpDep = Annotated[Client, Depends(get_request_explorer_http)]
BundlerDep = Annotated[Client, Depends(get_request_bundler)]
