!> configuration node: an expanded argument in C++ (expand: tixi handle + XML path)
module node_mod
  implicit none
  private
  public :: ConfigNode_type

  type :: ConfigNode_type
    integer :: tixiHandle = 0
    character(len=:), allocatable :: xml_path
  end type ConfigNode_type
end module node_mod
