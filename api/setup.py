import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="codex-dop",
    version="0.0.1",
    author="DevOps Pass AI",
    author_email="sre@devopspass-ai.com",
    description="Package to install DevOps Pass AI Codex",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: Private",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.10',
)