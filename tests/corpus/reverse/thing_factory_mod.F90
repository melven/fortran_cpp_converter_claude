!> factories of things with a registration by name (wrap_reverse_factories)
module thing_factory_mod
  use config_mod, only: Config_type
  use thing_mod, only: AbstractThing_type
  implicit none
  private
  public :: AbstractThingFactory_type, register_thing_factory, unregister_thing_factory, create_thing_by_name

  !> creates things
  type, abstract :: AbstractThingFactory_type
  contains
    procedure(create_iface), deferred :: create_thing
  end type AbstractThingFactory_type

  abstract interface
    !> a new thing for the configuration
    subroutine create_iface(this, config, thing, ierr)
      import :: AbstractThingFactory_type, AbstractThing_type, Config_type
      class(AbstractThingFactory_type), intent(in) :: this
      type(Config_type), intent(in) :: config
      class(AbstractThing_type), allocatable, intent(out) :: thing
      integer, intent(out) :: ierr
    end subroutine create_iface
  end interface

  type :: Entry_type
    character(len=:), allocatable :: name
    class(AbstractThingFactory_type), allocatable :: factory
  end type Entry_type
  type(Entry_type), allocatable, save :: factories(:)

contains

  subroutine register_thing_factory(name, factory)
    character(len=*), intent(in) :: name
    class(AbstractThingFactory_type), intent(in) :: factory
    type(Entry_type), allocatable :: tmp(:)
    integer :: n
    n = 0
    if (allocated(factories)) n = size(factories)
    allocate(tmp(n + 1))
    if (n > 0) tmp(1:n) = factories
    allocate(tmp(n + 1)%name, source=name)
    allocate(tmp(n + 1)%factory, source=factory)
    call move_alloc(tmp, factories)
  end subroutine register_thing_factory

  subroutine unregister_thing_factory(name)
    character(len=*), intent(in) :: name
    type(Entry_type), allocatable :: tmp(:)
    integer :: i, k
    if (.not. allocated(factories)) return
    allocate(tmp(count([(factories(i)%name /= name, i = 1, size(factories))])))
    k = 0
    do i = 1, size(factories)
      if (factories(i)%name /= name) then
        k = k + 1
        tmp(k) = factories(i)
      end if
    end do
    call move_alloc(tmp, factories)
  end subroutine unregister_thing_factory

  subroutine create_thing_by_name(name, config, thing, ierr)
    character(len=*), intent(in) :: name
    type(Config_type), intent(in) :: config
    class(AbstractThing_type), allocatable, intent(out) :: thing
    integer, intent(out) :: ierr
    integer :: i
    ierr = -1
    if (.not. allocated(factories)) return
    do i = 1, size(factories)
      if (factories(i)%name == name) then
        call factories(i)%factory%create_thing(config, thing, ierr)
        return
      end if
    end do
  end subroutine create_thing_by_name
end module thing_factory_mod
