#include "project_macros.h"
!> Callback implementations with user data: become methods of the user data type in C++.
module problem_mod
  use solver_mod, only: fixed_point_solve, fixed_point_type
  implicit none
  private

  public :: problem_type, solve_problem, solve_shifted

  type :: problem_type
    real :: a = 0.5         !< damping
    real :: target = 3.0
  contains
    procedure :: solve => problem_solve
  end type problem_type

contains

  !> residual f = a * (x - target)
  subroutine problem_residual(userData, x, f, ierr)
    class(*), intent(in)  :: userData
    real,     intent(in)  :: x(:)
    real,     intent(out) :: f(:)
    integer,  intent(out) :: ierr

    ierr = 0
    select type (userData)
    type is (problem_type)
      f = userData%a * (x - userData%target)
    class default
      PROJECT_ASSERT(.false., ierr)
    end select
  end subroutine problem_residual

  subroutine problem_solve(this, x, ierr)
    class(problem_type), intent(in) :: this
    real, intent(inout) :: x(:)
    integer, intent(out) :: ierr
    PROJECT_CHK_IERR(call fixed_point_solve(problem_residual, this, x, 1.e-12, 100, ierr), ierr)
  end subroutine problem_solve

  subroutine solve_problem(prob, x, ierr)
    type(problem_type), intent(in) :: prob
    real, intent(inout) :: x(:)
    integer, intent(out) :: ierr
    type(fixed_point_type) :: fp
    PROJECT_CHK_IERR(call fp%solve(problem_residual, prob, x, ierr), ierr)
  end subroutine solve_problem

  !> an internal procedure as callback, with the data from the host
  subroutine solve_shifted(prob, shift, x, ierr)
    type(problem_type), intent(in) :: prob
    real, intent(in) :: shift
    real, intent(inout) :: x(:)
    integer, intent(out) :: ierr
    PROJECT_CHK_IERR(call fixed_point_solve(shifted_residual, prob, x, 1.e-12, 100, ierr), ierr)
  contains
    subroutine shifted_residual(userData, x, f, ierr)
      class(*), intent(in) :: userData
      real, intent(in) :: x(:)
      real, intent(out) :: f(:)
      integer, intent(out) :: ierr
      ierr = 0
      f = prob%a * (x - prob%target - shift)
    end subroutine shifted_residual
  end subroutine solve_shifted

end module problem_mod
