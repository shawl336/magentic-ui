from .web_surfer import WebSurfer, WebSurferCUA
from ._coder import CoderAgent
from ._user_proxy import USER_PROXY_DESCRIPTION
from .file_surfer import FileSurfer
from .electrical_docgen import ElectrialcalTechnicalSpecificationGenerator, ElectrialcalProjectDesignGenerator
from ._coding_delegator import CodingDelegatorAgent
from .electrical_docgen import ElectricalRequirementValidator
from ._electrical_design_dummy import ElectricalDesignAgent
from ._material_selection import MaterialSelectionAgent
from ._open_creo import OpenCreoAgent

__all__ = [
    "WebSurfer",
    "WebSurferCUA",
    "CoderAgent",
    "USER_PROXY_DESCRIPTION",
    "FileSurfer",
    "ElectrialcalTechnicalSpecificationGenerator", "ElectrialcalProjectDesignGenerator",
    "CodingDelegatorAgent",
    "ElectricalRequirementValidator",
    "ElectricalDesignAgent",
    "MaterialSelectionAgent",
    "OpenCreoAgent"
]
