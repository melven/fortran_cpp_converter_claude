#include "project_macros.h"
!> checkpoints: black-box objects owned by C++ (wrap_owned)
module state_mod
  implicit none
  !> internal state of a model (only stored and passed back)
  type :: InternalStateWrapper_type
    class(*), allocatable :: p
  end type InternalStateWrapper_type

  type :: Model_type
    integer :: nCheckpoints = 2
    real :: x = 1.0
  contains
    procedure :: save => model_save
    procedure :: restore => model_restore
  end type Model_type
contains
  subroutine model_save(this, checkpoints, ierr)
    class(Model_type), intent(in) :: this
    type(InternalStateWrapper_type), allocatable, intent(out) :: checkpoints(:)
    integer, intent(out) :: ierr
    integer :: i
    ierr = 0
    PROJECT_ALLOCATE(checkpoints(this%nCheckpoints), ierr)
    do i = 1, size(checkpoints)
      allocate(checkpoints(i)%p, source=this%x * i)
    end do
  end subroutine model_save

  subroutine model_restore(this, checkpoints, ierr)
    class(Model_type), intent(inout) :: this
    type(InternalStateWrapper_type), intent(in) :: checkpoints(:)
    integer, intent(out) :: ierr
    ierr = 0
    select type (q => checkpoints(size(checkpoints))%p)
    type is (real)
      this%x = q
    end select
  end subroutine model_restore
end module state_mod
