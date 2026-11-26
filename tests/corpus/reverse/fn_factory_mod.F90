!> factory with functions as factory methods (one with an expanded argument) and a Fortran-only base type
module fn_factory_mod
  use config_mod, only: Config_type
  use thing_mod, only: AbstractThing_type
  use dict_mod, only: DictEntry_type
  use node_mod, only: ConfigNode_type
  implicit none
  private
  public :: ThingMaker_type, register_maker, unregister_maker, make_thing

  !> makes things; an entry of the dictionary of makers
  type, abstract, extends(DictEntry_type) :: ThingMaker_type
  contains
    procedure(make_iface), deferred :: make
    procedure(make_from_node_iface), deferred :: make_from_node
  end type ThingMaker_type

  abstract interface
    !> a new thing for the configuration
    function make_iface(this, config) result(thing)
      import :: ThingMaker_type, AbstractThing_type, Config_type
      class(ThingMaker_type), intent(in) :: this
      type(Config_type), intent(in) :: config
      class(AbstractThing_type), allocatable :: thing
    end function make_iface

    !> a new thing for the configuration node (an expanded argument in C++)
    function make_from_node_iface(this, node) result(thing)
      import :: ThingMaker_type, AbstractThing_type, ConfigNode_type
      class(ThingMaker_type), intent(in) :: this
      type(ConfigNode_type), intent(in) :: node
      class(AbstractThing_type), allocatable :: thing
    end function make_from_node_iface
  end interface

  class(ThingMaker_type), allocatable, save :: maker

contains

  subroutine register_maker(name, factory)
    character(len=*), intent(in) :: name
    class(ThingMaker_type), intent(in) :: factory
    allocate(maker, source=factory)
    allocate(maker%key, source=name)
  end subroutine register_maker

  subroutine unregister_maker(name)
    character(len=*), intent(in) :: name
    if (allocated(maker)) then
      if (maker%key == name) deallocate(maker)
    end if
  end subroutine unregister_maker

  !> a thing of the registered maker
  function make_thing(config) result(thing)
    type(Config_type), intent(in) :: config
    class(AbstractThing_type), allocatable :: thing
    if (allocated(maker)) allocate(thing, source=maker%make(config))     ! (-fno-realloc-lhs)
  end function make_thing
end module fn_factory_mod
