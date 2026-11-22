from __future__ import annotations

import pytest
from ase2sprkkr.input_parameters.input_parameters import InputParameters

from ase2sprkkr_gui.gui.input_parameters.validation import validate_setup


@pytest.mark.parametrize(
    ("created_task", "effective_task", "should_reject"),
    (
        pytest.param("SPLEED", "ARPES", True, id="effective-arpes"),
        pytest.param("ARPES", "SPLEED", False, id="effective-spleed"),
    ),
)
def test_angular_scan_validation_follows_effective_task(
    created_task, effective_task, should_reject,
):
    parameters = InputParameters.create(created_task)
    assert parameters.task_name.upper() == created_task

    parameters.TASK.TASK = effective_task
    parameters.SPEC_EL.THETA = [-20.0, 20.0]
    assert parameters.task_definition.name.upper() == effective_task

    if should_reject:
        with pytest.raises(ValueError, match="consistent angular scan"):
            validate_setup(parameters)
    else:
        validate_setup(parameters)
