"""Build the C++ replay extension in place:  python build_ext.py build_ext --inplace   (needs a C++17 compiler)"""
from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup

setup(name="futdesk-ext", ext_modules=[Pybind11Extension("futdesk._replay", ["cpp/replay.cpp"], cxx_std=17)], cmdclass={"build_ext": build_ext}, zip_safe=False)
