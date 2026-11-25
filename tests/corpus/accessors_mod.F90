!> member accessors of translated types (member_accessors)
module accessors_mod
  implicit none
  private
  public :: solver_type, special_type, run

  type :: solver_type
    integer :: n = 0                  !< problem size
    real :: tol = 1.0e-6              !< tolerance
    real, allocatable :: x(:)         !< state
    integer :: count = 0              !< not listed: plain member
  contains
    procedure :: init => solver_init
    procedure :: norm => solver_norm
  end type solver_type

  type, extends(solver_type) :: special_type
  contains
    procedure :: scale => special_scale
  end type special_type

contains

  subroutine solver_init(this, n)
    class(solver_type), intent(inout) :: this
    integer, intent(in) :: n
    this%n = n
    allocate(this%x(n))
    this%x = 1.0
  end subroutine solver_init

  function solver_norm(this) result(r)
    class(solver_type), intent(in) :: this
    real :: r
    r = sqrt(sum(this%x**2)) * this%tol
  end function solver_norm

  subroutine special_scale(this, f)
    class(special_type), intent(inout) :: this
    real, intent(in) :: f
    this%x = f * this%x                ! member of the base class: x_ (protected)
  end subroutine special_scale

  function run(s) result(r)
    type(solver_type), intent(inout) :: s
    real :: r
    s%tol = 0.5                        ! setter
    s%x(1) = 2.0                       ! reference
    s%count = s%count + s%n            ! getter (read) / plain member
    s%n = 3                            ! read-only: TODO
    r = s%norm() + s%tol
  end function run
end module accessors_mod
