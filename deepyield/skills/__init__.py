"""Only the extracted Release skill adapters are registered."""
from deepyield.skills.registry import get_skill, run_skill, list_skills, register_skill
from deepyield.skills import release_cpk, release_cgm, release_mmsa, release_cpk_excel
from deepyield.skills import release_cgm_excel
