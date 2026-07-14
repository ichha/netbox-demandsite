from setuptools import find_packages, setup

setup(
    name='netbox-demandsite',
    version='0.1.0',
    description='NetBox plugin to view and sync site data from Demandsite API',
    author='Antigravity Developer',
    license='Apache 2.0',
    install_requires=['requests'],
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
)
