"""FAST request correction package.

Import concrete services from their defining modules. Keeping this package
initializer side-effect free prevents replay and rebuild modules from loading
one another through an eager facade.
"""
