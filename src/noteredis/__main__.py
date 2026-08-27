"""Entry point: ``python -m noteredis -f {connection_file}``."""

from ipykernel.kernelapp import IPKernelApp

from .kernel import RedisKernel

if __name__ == "__main__":
    IPKernelApp.launch_instance(kernel_class=RedisKernel)
