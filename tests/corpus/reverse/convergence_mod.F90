!> abstract model with constant sizes (wrap_reverse + wrap_constants)
#include "project_macros.h"
module convergence_mod
  use model_base_mod, only: ModelBase_type
  implicit none
  private
  public :: Thing_type

  type, abstract, extends(ModelBase_type) :: Thing_type
    integer :: inputSize = 1   !< size of the input
  contains
    procedure :: check_convergence
  end type Thing_type

contains

  !> convergence check (default: converged)
  subroutine check_convergence(this, state, input, time, reset, needAnotherIteration, ierr)
    class(Thing_type), intent(inout)  :: this                 !< the model
    real,            contiguous, target, intent(in)     :: state(:)             !< new state, dimension (this\%stateSize)
    real,            contiguous, target, intent(in)     :: input(:)             !< new input, dimension (this\%inputSize)
    real,                                intent(in)     :: time                 !< current time
    logical,                             intent(in)     :: reset                !< indicate that all old values are obsolete
    logical,                             intent(out)    :: needAnotherIteration !< indicate that another iteration is required
    integer,                             intent(out)    :: ierr                 !< error flag
    !----------------------------------------------------------------------
    PROJECT_CHK_ARGUMENT(size(state) == this%stateSize, ierr)
    PROJECT_CHK_ARGUMENT(size(input) == this%inputSize, ierr)

    needAnotherIteration = .false.
    ierr = 0
  end subroutine check_convergence

end module convergence_mod
