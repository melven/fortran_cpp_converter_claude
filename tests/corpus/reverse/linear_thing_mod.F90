!> the implementation (translated to C++)
module linear_thing_mod
  use config_mod, only: Config_type
  use thing_mod, only: AbstractThing_type
  use thing_factory_mod, only: AbstractThingFactory_type
  implicit none
  private
  public :: LinearThing_type, LinearThingFactory_type

  type, extends(AbstractThing_type) :: LinearThing_type
    real :: factor = 1.0
  contains
    procedure :: evaluate => linear_evaluate
    procedure :: weight => linear_weight
  end type LinearThing_type

  type, extends(AbstractThingFactory_type) :: LinearThingFactory_type
  contains
    procedure :: create_thing => linear_create
  end type LinearThingFactory_type

contains

  subroutine linear_evaluate(this, x, y, ierr)
    class(LinearThing_type), intent(inout) :: this
    real, intent(in) :: x(:)
    real, intent(out) :: y(:)
    integer, intent(out) :: ierr
    ierr = 0
    y = this%factor * x
  end subroutine linear_evaluate

  function linear_weight(this) result(w)
    class(LinearThing_type), intent(in) :: this
    real :: w
    w = 2.0 * this%factor
  end function linear_weight

  subroutine linear_create(this, config, thing, ierr)
    class(LinearThingFactory_type), intent(in) :: this
    type(Config_type), intent(in) :: config
    class(AbstractThing_type), allocatable, intent(out) :: thing
    integer, intent(out) :: ierr
    ierr = 0
    allocate(LinearThing_type :: thing)
    select type (thing)
    type is (LinearThing_type)
      thing%factor = config%scale
    end select
  end subroutine linear_create
end module linear_thing_mod
